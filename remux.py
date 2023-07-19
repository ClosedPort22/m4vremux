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


def get_xml(tags):
    return XML_TEMPLATE.format(
        "".join(TAG_TEMPLATE.format(name=key, string=value)
                for key, value in tags.items() if value is not None))


def ffprobe(path, args, exe_path):
    sout = subprocess.check_output([
        exe_path, "-v", "error", "-of", "json=c=1", *args,
        path
    ])
    return json.loads(sout)


def write_text(path, text):
    with open(path, mode="w", encoding="utf-8") as f:
        f.write(text)


class InputFiles(defaultdict):
    """Path of input file -> a set of stream IDs for the file"""

    def __init__(self):
        super().__init__(set)

    @staticmethod
    def get_xml_path(path, tid):
        return f"{path}.{tid}.xml"

    def get_args(self):
        """Convert to a list of args suitable for calling the actual program"""
        out = []
        for key, value in self.items():
            # tags first
            for tid in value:
                out.extend(("--tags", f"{tid}:{self.get_xml_path(key, tid)}"))
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

    # write global tags
    global_tags = jobj["format"]["tags"]
    global_tags.update(override_global)
    write_text(global_tags_path, get_xml(global_tags))

    input_streams = InputFiles()

    for stream in jobj["streams"]:
        if stream["codec_name"] == "mjpeg":
            break  # assume cover is the last stream

        if stream["codec_type"] == "subtitle":
            # convert subtitles to SRT
            srt_str = subprocess.check_output(
                [ffmpeg_path, "-i", path, "-map", f"0:{stream['index']}",
                 "-scodec", "srt", "-f", "srt", "-"]).decode()
            # remove HTML tags
            srt_str = re.sub(
                r'<font face="[^"]+">(?:{\\an7})?([^<]+)</font>',
                r"\1", srt_str)

            file = f"{path}.sub_{stream['index']}.srt"
            delete.append(file)
            # write SRT to file
            write_text(file, srt_str)
            id = 0
        else:
            file = path
            id = stream["index"]

        # write track tags to file
        stream["tags"].update(override_track)
        write_text(
            input_streams.get_xml_path(file, id), get_xml(stream["tags"]))
        input_streams[file].add(id)

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
        "--override-global-tags", metavar="JSON",
        default='{"creation_time": null, "purchase_date": null}',
        help="A JSON object that will be parsed and merged into the global "
        "tag object. This can be used to change tags, add new ones, or "
        "redact sensitive information. Set a tag to 'null' to remove it. "
        "By default, 'creation_time' and 'purchase_date' are removed.")
    parser.add_argument(
        "--override-track-tags", metavar="JSON",
        default='{"creation_time": null}',
        help="A JSON object that will be parsed and merged into each "
        "track-specific tag object. See '--override-global-tags'. By default, "
        "'creation_time' is removed.")

    parser.add_argument(
        "--mkvmerge-raw-args", metavar="ARGS",
        help="Raw arguments passed to mkvmerge (string only!)")

    parser.add_argument("INPUT", help=argparse.SUPPRESS)
    parser.add_argument("OUTPUT", help=argparse.SUPPRESS)

    args = parser.parse_args()

    retcode, delete = remux(
        args.INPUT, args.OUTPUT, ffmpeg_path=args.ffmpeg_path,
        ffprobe_path=args.ffprobe_path, mkvmerge_path=args.mkvmerge_path,
        override_global=json.loads(args.override_global_tags),
        override_track=json.loads(args.override_track_tags),
        extra_args=shlex.split(args.mkvmerge_raw_args))
    if retcode == 0:
        print("Everything OK, deleting temp files:", delete)
        for file in delete:
            os.unlink(file)
    return retcode


if __name__ == "__main__":
    sys.exit(main())
