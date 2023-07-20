#!/usr/bin/env python3

import os
import re
import sys
import json
import shlex
import argparse
import subprocess
from collections import defaultdict


XML_TEMPLATE = """<?xml version="1.0"?>
<!-- <!DOCTYPE Tags SYSTEM "matroskatags.dtd"> -->
<Tags>
{}</Tags>
"""

TAG_TEMPLATE = """  <Tag>
    <Simple>
      <Name>{name}</Name>
      <String>{string}</String>
    </Simple>
  </Tag>
"""

_remove_font = re.compile(r'(?s)<font face=".+?">(?:{.+?})?(.+?)</font>').sub

is_snake_case = re.compile(r"[a-z]+(_[a-z]+)*").fullmatch


def sanitize_srt(txt):
    return _remove_font(r"\1", txt).replace("\\h", "")


def get_xml(tags):
    return XML_TEMPLATE.format("".join(TAG_TEMPLATE.format(
        # convert snake_case tags to SCREAMING_SNAKE_CASE as per
        # specification
        # https://www.matroska.org/technical/tagging.html
        name=key.upper() if is_snake_case(key) else key,
        string=value,
    ) for key, value in tags.items() if value is not None))


def ffprobe(path, args, exe_path):
    sout = subprocess.check_output([
        exe_path, "-v", "error", "-of", "json=c=1", *args,
        path
    ])
    return json.loads(sout)


def write_text(path, text):
    with open(path, mode="w", encoding="utf-8") as f:
        f.write(text)


class ParseAction(argparse.Action):
    """Custom `argparse` action that transforms the argument according
    to `parser`
    """

    def __init__(self, parser, *args, **kwargs):
        self._parser = parser
        super().__init__(*args, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, self._parser(values))


class InputFiles(defaultdict):
    """Path of input file -> a dict of (stream ID -> language code)"""

    def __init__(self):
        super().__init__(dict)

    @staticmethod
    def get_xml_path(path, tid):
        return f"{path}.{tid}.xml"

    def get_args(self):
        """Convert to a list of args suitable for calling the actual program"""
        out = []
        for key, value in self.items():
            # tags first
            for tid, lang in value.items():
                out.extend(("--tags", f"{tid}:{self.get_xml_path(key, tid)}"))
                # set language explicitly
                if lang:
                    out.extend(("--language", f"{tid}:{lang}"))
            out.extend(("(", key, ")"))
        return out

    def get_xmls(self):
        """Get a list of XML files"""
        out = []
        for key, value in self.items():
            for tid in value:
                out.append(self.get_xml_path(key, tid))
        return out


def remux(path, dest_path, ffmpeg_path, ffprobe_path,
          mkvmerge_path, override_global=(), override_track=(),
          extra_args=()):
    global_tags_path = f"{path}.global_tags.xml"
    delete = [global_tags_path]
    args = [mkvmerge_path, "--output", dest_path,
            "--global-tags", global_tags_path]

    jobj = ffprobe(
        path, args=["-show_streams", "-show_entries", "format"],
        exe_path=ffprobe_path)

    # write title
    try:
        args.extend(("--title", jobj["format"]["tags"]["title"]))
    except KeyError:
        pass

    # write global tags
    global_tags = jobj["format"]["tags"]
    global_tags.update(override_global)
    write_text(global_tags_path, get_xml(global_tags))

    input_streams = InputFiles()

    for stream in jobj["streams"]:
        if stream["codec_name"] == "mjpeg":
            break  # assume cover is the last stream

        if stream["codec_type"] == "subtitle":
            # convert subtitles to SRT and remove HTML tags
            srt_str = sanitize_srt(subprocess.check_output([
                ffmpeg_path, "-i", path, "-map", f"0:{stream['index']}",
                "-scodec", "srt", "-f", "srt", "-"
            ]).decode())

            file = f"{path}.sub_{stream['index']}.srt"
            delete.append(file)
            # write SRT to file
            write_text(file, srt_str)
            id = 0
        else:
            file = path
            id = stream["index"]

        stream_tags = stream["tags"]
        # write track tags to file
        stream_tags.update(override_track)
        write_text(
            input_streams.get_xml_path(file, id), get_xml(stream_tags))
        input_streams[file][id] = stream_tags.get("language")

    args.extend(input_streams.get_args())
    args.extend(extra_args)
    print("mkvmerge command line:", args)

    # fire
    proc = subprocess.run(args)

    # delete subtitles and XML files
    delete.extend(input_streams.get_xmls())
    return proc.returncode, delete


def main():
    parser = argparse.ArgumentParser(
        description="Remux m4v to mkv",
        usage="%(prog)s [OPTION]... INPUT OUTPUT")

    parser.add_argument(
        "--ffmpeg-path", default="ffmpeg", metavar="PATH",
        help="Path to FFmpeg executable")
    parser.add_argument(
        "--ffprobe-path", default="ffprobe", metavar="PATH",
        help="Path to FFprobe executable")
    parser.add_argument(
        "--mkvmerge-path", default="mkvmerge", metavar="PATH",
        help="Path to mkvmerge executable")

    parser.add_argument(
        "--override-global-tags", metavar="JSON", action=ParseAction,
        parser=json.loads,
        default={"creation_time": None, "purchase_date": None},
        help="A JSON object that will be parsed and merged into the global "
        "tag object. This can be used to change tags, add new ones, or "
        "redact sensitive information. Set a tag to 'null' to remove it. "
        "By default, 'creation_time' and 'purchase_date' are removed. Set "
        "this option to '{}' to disable this.")
    parser.add_argument(
        "--override-track-tags", metavar="JSON", action=ParseAction,
        parser=json.loads, default={"creation_time": None},
        help="A JSON object that will be parsed and merged into each "
        "track-specific tag object. See '--override-global-tags'. By default, "
        "'creation_time' is removed.")

    parser.add_argument(
        "--mkvmerge-raw-args", metavar="ARGS", action=ParseAction,
        parser=shlex.split,
        help="Raw arguments passed to mkvmerge (string only!)")

    parser.add_argument("INPUT", help=argparse.SUPPRESS)
    parser.add_argument("OUTPUT", help=argparse.SUPPRESS)

    args = parser.parse_args()

    retcode, delete = remux(
        args.INPUT, args.OUTPUT, ffmpeg_path=args.ffmpeg_path,
        ffprobe_path=args.ffprobe_path, mkvmerge_path=args.mkvmerge_path,
        override_global=args.override_global_tags,
        override_track=args.override_track_tags,
        extra_args=args.mkvmerge_raw_args)
    if retcode == 0:
        print("Everything OK, deleting temp files:", delete)
        for file in delete:
            os.unlink(file)
    return retcode


if __name__ == "__main__":
    sys.exit(main())
