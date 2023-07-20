# m4vremux
Remux m4v to mkv while keeping as much metadata as possible


## Usage
See `remux.py --help`


## Dependencies
* `FFmpeg`
* `FFprobe`
* `mkvmerge`


## Known Issues
* Some programs have issues displaying track-specific tags because `mkvmerge`
  writes tags at the very end of the file
  (https://web.archive.org/web/20230610183703/https://www.reddit.com/r/mkvtoolnix/comments/9egh2i/the_state_of_mkv_tagging_and_not_being_able_to/)


## License
MIT License
