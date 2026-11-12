#!/usr/bin/env python3
import argparse
import fcntl
import math
import os
import select
import sys
import time
from array import array
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image, ImageOps, ImageSequence
except ImportError:
    Image = None


class KeyboardError(Exception):
    pass


def open_hidraw(path):
    try:
        return os.open(path, os.O_RDWR)
    except PermissionError:
        raise KeyboardError(
            f"no permission to open {path}; add a udev rule such as "
            'KERNEL=="hidraw*", ATTRS{idVendor}=="0c45", ATTRS{idProduct}=="8009", TAG+="uaccess"'
        ) from None


class HexColour:
    @staticmethod
    def parse(text):
        digits = text.strip().removeprefix("#")
        if len(digits) == 3:
            digits = "".join(digit * 2 for digit in digits)
        try:
            if len(digits) != 6:
                raise ValueError
            return tuple(bytes.fromhex(digits))
        except ValueError:
            raise argparse.ArgumentTypeError(f"{text!r} is not a hex colour like ff8800 or #ff8800") from None

    @staticmethod
    def format(colour):
        return "#{:02X}{:02X}{:02X}".format(*colour)


class HidrawInterface:
    SYSFS_ROOT = Path("/sys/class/hidraw")

    def __init__(self, sysfs_node):
        properties = dict(
            line.split("=", 1)
            for line in (sysfs_node / "device/uevent").read_text().splitlines()
            if "=" in line
        )
        self.device_path = Path("/dev", sysfs_node.name)
        self.hid_id = properties.get("HID_ID", "")
        self.physical_path = properties.get("HID_PHYS", "")
        self.report_descriptor = (sysfs_node / "device/report_descriptor").read_bytes()

    @property
    def usb_device(self):
        return self.physical_path.rpartition("/input")[0]

    @classmethod
    def all(cls):
        interfaces = []
        for node in sorted(cls.SYSFS_ROOT.glob("hidraw*")):
            try:
                interfaces.append(cls(node))
            except OSError:
                continue
        return interfaces


class FeatureChannel:
    REPORT_SIZE = 64
    SEND_DELAY = 0.035
    REPLY_DELAY = 0.05
    SET_FEATURE = 0x06
    GET_FEATURE = 0x07

    def __init__(self, path):
        self.path = path
        self.descriptor = open_hidraw(path)
        fcntl.flock(self.descriptor, fcntl.LOCK_EX)

    def close(self):
        os.close(self.descriptor)

    @staticmethod
    def _ioctl_request(number, length):
        return (3 << 30) | (length << 16) | (ord("H") << 8) | number

    def exchange(self, packet):
        report_length = self.REPORT_SIZE + 1
        time.sleep(self.SEND_DELAY)
        fcntl.ioctl(self.descriptor, self._ioctl_request(self.SET_FEATURE, report_length), bytes(1) + packet)
        time.sleep(self.REPLY_DELAY)
        reply = bytearray(report_length)
        if fcntl.ioctl(self.descriptor, self._ioctl_request(self.GET_FEATURE, report_length), reply) != report_length:
            raise KeyboardError("the keyboard sent a short feature reply")
        return bytes(reply[1:])


class DisplayChannel:
    CHUNK_SIZE = 4096
    REPORT_SIZE = 64
    ACK_TIMEOUT = 1.0

    def __init__(self, path):
        self.descriptor = open_hidraw(path)
        while select.select([self.descriptor], [], [], 0)[0]:
            os.read(self.descriptor, self.REPORT_SIZE)

    def __enter__(self):
        return self

    def __exit__(self, *exception_info):
        os.close(self.descriptor)

    def send(self, chunk):
        report = bytes(1) + chunk
        if os.write(self.descriptor, report) != len(report):
            raise KeyboardError("short write to the keyboard screen")

    def acknowledged(self):
        if not select.select([self.descriptor], [], [], self.ACK_TIMEOUT)[0]:
            return False
        os.read(self.descriptor, self.REPORT_SIZE)
        return True


class AlignedClockPacket:
    def __init__(self, lead_time):
        self.lead_time = lead_time
        self.moment = None

    def __iter__(self):
        now = time.time()
        next_second = math.floor(now) + 1
        if next_second - now < self.lead_time:
            next_second += 1
        time.sleep(next_second - self.lead_time - now)
        self.moment = datetime.fromtimestamp(next_second)
        yield Ak820Pro.clock_packet(self.moment)


class Ak820Pro:
    VENDOR_ID = "00000C45"
    PRODUCT_IDS = {"00008009", "0000800A"}
    CONTROL_DESCRIPTOR = bytes.fromhex(
        "0613ff0901a101150026ff007508954009028102090391020a00ffb102c0"
    )
    DISPLAY_DESCRIPTOR = bytes.fromhex(
        "0668ff0961a1010962150026ff009540750881020963150026ff0096001075089102c0"
    )

    START = 0x18
    LIGHTING = 0x13
    CLOCK = 0x28
    IMAGE = 0x72
    COMMIT = 0x02
    FINISH = 0xF0

    SETTINGS_FLAG = 1
    UPLOAD_FLAG = 0
    START_ATTEMPTS = 10
    START_RETRY_DELAY = 0.3
    UPLOAD_SETTLE_TIME = 0.1

    BREATHING_EFFECT = 0x07
    MAX_BRIGHTNESS = 5
    SCREEN_SLOTS = (1, 2, 3)

    def __init__(self, control_path, display_path):
        self.control_path = control_path
        self.display_path = display_path
        self.control = FeatureChannel(control_path)

    def __enter__(self):
        return self

    def __exit__(self, *exception_info):
        self.control.close()

    @classmethod
    def locate(cls):
        interfaces = [interface for interface in HidrawInterface.all() if cls._matches_usb_id(interface.hid_id)]
        if not interfaces:
            raise KeyboardError("Ajazz AK820 Pro (USB 0c45:8009) not found; it must be connected by cable")
        for usb_device in dict.fromkeys(interface.usb_device for interface in interfaces):
            paths_by_descriptor = {
                interface.report_descriptor: interface.device_path
                for interface in interfaces
                if interface.usb_device == usb_device
            }
            if cls.CONTROL_DESCRIPTOR in paths_by_descriptor and cls.DISPLAY_DESCRIPTOR in paths_by_descriptor:
                return paths_by_descriptor[cls.CONTROL_DESCRIPTOR], paths_by_descriptor[cls.DISPLAY_DESCRIPTOR]
        raise KeyboardError("found an AK820, but its HID interfaces differ from the supported firmware; nothing was sent")

    @classmethod
    def open(cls):
        return cls(*cls.locate())

    @classmethod
    def _matches_usb_id(cls, hid_id):
        fields = hid_id.split(":")
        return len(fields) == 3 and fields[1] == cls.VENDOR_ID and fields[2] in cls.PRODUCT_IDS

    @staticmethod
    def command_packet(command, subcommand=0, value=0):
        packet = bytearray(FeatureChannel.REPORT_SIZE)
        packet[0:3] = bytes([0x04, command, subcommand])
        packet[8:10] = value.to_bytes(2, "little")
        return bytes(packet)

    @classmethod
    def static_colour_packet(cls, colour, brightness):
        packet = bytearray(FeatureChannel.REPORT_SIZE)
        packet[0] = cls.BREATHING_EFFECT
        packet[1:4] = bytes(colour)
        packet[9] = brightness
        packet[14:16] = b"\x55\xaa"
        return bytes(packet)

    @staticmethod
    def clock_packet(moment):
        if not 2000 <= moment.year <= 2099:
            raise KeyboardError(f"the keyboard clock cannot hold the year {moment.year}")
        packet = bytearray(FeatureChannel.REPORT_SIZE)
        packet[1:9] = bytes([
            0x01, 0x5A, moment.year - 2000, moment.month, moment.day,
            moment.hour, moment.minute, moment.second,
        ])
        packet[10] = 0x04
        packet[62:64] = b"\xaa\x55"
        return bytes(packet)

    def _check_reply(self, reply, command):
        if reply[0] != 0x04 or reply[1] != command or reply[3] != 0x01:
            raise KeyboardError(f"the keyboard refused command 0x{command:02x} (reply {reply[:9].hex(' ')})")

    def _send_command(self, command, subcommand=0, value=0):
        self._check_reply(self.control.exchange(self.command_packet(command, subcommand, value)), command)

    def _start_session(self, flag):
        busy_reply = bytes([0x04, self.START, 0x00, 0xFF])
        start_packet = self.command_packet(self.START, value=flag)
        for _ in range(self.START_ATTEMPTS - 1):
            reply = self.control.exchange(start_packet)
            if any(reply) and reply[:4] != busy_reply:
                self._check_reply(reply, self.START)
                return
            time.sleep(self.START_RETRY_DELAY)
        self._check_reply(self.control.exchange(start_packet), self.START)

    def _finish_session(self):
        self._send_command(self.FINISH, value=self.SETTINGS_FLAG)

    def _abandon_session(self):
        try:
            self._finish_session()
        except (KeyboardError, OSError):
            pass

    def _write_setting(self, command, data_packets):
        try:
            self._start_session(self.SETTINGS_FLAG)
            self._send_command(command, value=1)
            for packet in data_packets:
                self.control.exchange(packet)
            self._send_command(self.COMMIT)
        except BaseException:
            self._abandon_session()
            raise
        self._finish_session()

    def set_static_colour(self, colour, brightness):
        self._write_setting(self.LIGHTING, [self.static_colour_packet(colour, brightness)])

    def sync_clock(self):
        clock_packet = AlignedClockPacket(FeatureChannel.SEND_DELAY)
        self._write_setting(self.CLOCK, clock_packet)
        return clock_packet.moment

    def upload_animation(self, animation, slot, on_progress=None):
        payload = animation.encode()
        chunk_size = DisplayChannel.CHUNK_SIZE
        chunk_count = len(payload) // chunk_size
        with DisplayChannel(self.display_path) as display:
            self._start_session(self.UPLOAD_FLAG)
            try:
                self._send_command(self.IMAGE, slot, chunk_count)
                for index in range(chunk_count):
                    display.send(payload[index * chunk_size:(index + 1) * chunk_size])
                    if not display.acknowledged():
                        raise KeyboardError(
                            f"no acknowledgement for screen chunk {index + 1}/{chunk_count}; upload stopped"
                        )
                    if on_progress:
                        on_progress(index + 1, chunk_count)
                self._send_command(self.COMMIT)
            except BaseException:
                self._abandon_session()
                raise
        time.sleep(self.UPLOAD_SETTLE_TIME)


@dataclass
class Frame:
    image: object
    duration_ms: int


class ScreenFitter:
    SIZE = (128, 128)
    MODES = ("crop", "pad", "stretch")

    def __init__(self, mode):
        self.mode = mode

    def fit(self, image):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        flattened = Image.alpha_composite(background, rgba).convert("RGB")
        resampling = Image.Resampling.LANCZOS
        if self.mode == "crop":
            return ImageOps.fit(flattened, self.SIZE, resampling)
        if self.mode == "pad":
            return ImageOps.pad(flattened, self.SIZE, resampling, color=(0, 0, 0))
        return flattened.resize(self.SIZE, resampling)


class Animation:
    HEADER_SIZE = 256
    FRAME_SIZE = ScreenFitter.SIZE[0] * ScreenFitter.SIZE[1] * 2
    MAX_FRAMES = 30
    DELAY_UNIT_MS = 2
    MAX_DELAY_MS = 255 * DELAY_UNIT_MS
    DEFAULT_DELAY_MS = 100
    MIN_SOURCE_DELAY_MS = 20

    def __init__(self, frames, source_frame_count):
        self.frames = frames
        self.source_frame_count = source_frame_count

    @classmethod
    def load(cls, path, fitter):
        if Image is None:
            raise KeyboardError("screen images need Pillow (the python-pillow package)")
        try:
            with Image.open(path) as source:
                source_frame_count = getattr(source, "n_frames", 1)
                kept_frame_count = min(source_frame_count, cls.MAX_FRAMES)
                frames = []
                for index, source_frame in enumerate(ImageSequence.Iterator(source)):
                    duration = cls._duration_of(source_frame)
                    if index * kept_frame_count // source_frame_count == len(frames):
                        frames.append(Frame(fitter.fit(source_frame), duration))
                    else:
                        frames[-1].duration_ms += duration
        except OSError as error:
            raise KeyboardError(f"cannot read {path}: {error}") from None
        return cls(frames, source_frame_count)

    @classmethod
    def _duration_of(cls, frame):
        duration = int(frame.info.get("duration") or 0)
        return duration if duration >= cls.MIN_SOURCE_DELAY_MS else cls.DEFAULT_DELAY_MS

    @property
    def is_animated(self):
        return len(self.frames) > 1

    @property
    def chunk_count(self):
        return math.ceil((self.HEADER_SIZE + len(self.frames) * self.FRAME_SIZE) / DisplayChannel.CHUNK_SIZE)

    def describe(self):
        frame_count = len(self.frames)
        lines = [f"{frame_count} frame{'s' if self.is_animated else ''}, "
                 f"{ScreenFitter.SIZE[0]}x{ScreenFitter.SIZE[1]}, {self.chunk_count} chunks to send"]
        if self.source_frame_count > frame_count:
            lines.append(f"the source has {self.source_frame_count} frames and the keyboard holds "
                         f"{self.MAX_FRAMES}, so frames were dropped evenly and the timing kept")
        if self.is_animated and any(frame.duration_ms > self.MAX_DELAY_MS for frame in self.frames):
            lines.append(f"frames longer than {self.MAX_DELAY_MS} ms are shortened to {self.MAX_DELAY_MS} ms")
        return lines

    def encode(self):
        header = bytearray(b"\xff" * self.HEADER_SIZE)
        header[0] = len(self.frames)
        if self.is_animated:
            for index, frame in enumerate(self.frames):
                header[index + 1] = max(1, min(255, frame.duration_ms // self.DELAY_UNIT_MS))
        else:
            header[1] = 0
        payload = bytes(header) + b"".join(self._rgb565(frame.image) for frame in self.frames)
        return payload + b"\xff" * (-len(payload) % DisplayChannel.CHUNK_SIZE)

    @staticmethod
    def _rgb565(image):
        rgb = image.tobytes()
        pixels = array("H", (
            (red >> 3) << 11 | (green >> 2) << 5 | blue >> 3
            for red, green, blue in zip(rgb[0::3], rgb[1::3], rgb[2::3])
        ))
        if sys.byteorder == "big":
            pixels.byteswap()
        return pixels.tobytes()

    def save_preview(self, path):
        images = [frame.image for frame in self.frames]
        durations = [min(frame.duration_ms, self.MAX_DELAY_MS) for frame in self.frames]
        images[0].save(path, save_all=self.is_animated, append_images=images[1:], duration=durations, loop=0)


class KeyboardCli:
    @classmethod
    def register(cls, parser):
        commands = parser.add_subparsers(dest="keyboard_command", required=True, metavar="{colour,image,time}")

        colour_parser = commands.add_parser("colour", aliases=["color"], help="light every key in one static colour")
        colour_parser.add_argument("colour", type=HexColour.parse, metavar="HEX",
                                   help="hex code, e.g. ff8800 or '#ff8800'")
        colour_parser.add_argument("-b", "--brightness", type=int, default=Ak820Pro.MAX_BRIGHTNESS,
                                   choices=range(Ak820Pro.MAX_BRIGHTNESS + 1), help="0-5 (default: 5)")
        colour_parser.set_defaults(handler=cls.set_colour)

        image_parser = commands.add_parser("image", help="show a GIF, PNG or JPEG on the screen")
        image_parser.add_argument("path", type=Path, help="picture or animated GIF; it is resized to 128x128")
        image_parser.add_argument("--fit", choices=ScreenFitter.MODES, default="crop",
                                  help="crop to fill the screen (default), pad with black, or stretch")
        image_parser.add_argument("--slot", type=int, choices=Ak820Pro.SCREEN_SLOTS, default=1,
                                  help="screen slot to replace (default: 1)")
        image_parser.add_argument("--preview", type=Path, metavar="FILE",
                                  help="also save the resized result to FILE")
        image_parser.add_argument("-n", "--dry-run", action="store_true", help="prepare the image, send nothing")
        image_parser.set_defaults(handler=cls.upload_image)

        time_parser = commands.add_parser("time", help="set the keyboard clock to this computer's local time")
        time_parser.set_defaults(handler=cls.sync_time)

    @staticmethod
    def set_colour(args):
        with Ak820Pro.open() as keyboard:
            keyboard.set_static_colour(args.colour, args.brightness)
        print(f"Keyboard: static {HexColour.format(args.colour)}, "
              f"brightness {args.brightness}/{Ak820Pro.MAX_BRIGHTNESS}")

    @staticmethod
    def sync_time(args):
        with Ak820Pro.open() as keyboard:
            moment = keyboard.sync_clock()
        print(f"Keyboard clock set to {moment:%Y-%m-%d %H:%M:%S}")

    @classmethod
    def upload_image(cls, args):
        animation = Animation.load(args.path, ScreenFitter(args.fit))
        print(f"Screen, {args.path}:")
        for line in animation.describe():
            print(f"  {line}")
        if args.preview:
            animation.save_preview(args.preview)
            print(f"  preview saved to {args.preview}")
        control_path, display_path = Ak820Pro.locate()
        if args.dry_run:
            print(f"  would replace slot {args.slot} through {control_path} and {display_path}")
            return
        print("  uploading; keep the keyboard plugged in until it finishes")
        with Ak820Pro(control_path, display_path) as keyboard:
            keyboard.upload_animation(animation, args.slot, cls._show_progress)
        print(f"\n  slot {args.slot} replaced")

    @staticmethod
    def _show_progress(sent, total):
        print(f"\r  sent {sent}/{total} chunks", end="", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="Control the Ajazz AK820 Pro: static colour, screen image and clock."
    )
    KeyboardCli.register(parser)
    args = parser.parse_args()
    try:
        args.handler(args)
    except (KeyboardError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nerror: interrupted", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
