---
name: lap-video
description: F1 25 best lap video in a fixed format: a clip from the replay, timer and telemetry on top, a driver title card with the Driver Song. Use when the user asks (in any language) to make a video of a fast or best lap ("make a lap video", "fastest lap video", "I recorded a lap from the replay", «сделай видео круга», «ролик fastest lap», «записал круг из повтора»), or sends a lap recording .mp4.
---

# Best lap video

Reply in the user's language. The format is always the same, it is defined by `tools/lap_video.py`. Don't change the design for a single video.
If the user asks to change the format permanently, change the script and this file.

## Personal settings

The user's settings live in `local/lap-video.md` (not in git). Read it first and follow its notes (for example reminders to give the user).
It holds:

- the driver: values for `--first` and `--last`;
- the Driver Song: path to the mp3. The file is already trimmed, plays from the start, the title card lasts as long as the song;
- the folder where OBS saves recordings;
- notes and reminders.

If the file does not exist, ask the user for these values and create it in this form:

```markdown
# Lap video: personal settings

- Driver: `--first <First> --last <LAST>`
- Driver Song: `<path to mp3>`
- OBS saves to: `<folder>`

## Notes
```

The team and the Grand Prix come from the telemetry, the script picks `FASTEST LAP` or `BEST LAP` itself from the race results.

## What is needed from the user

1. Open the replay in Theatre (`Documents\My Games\F1 25\replays`), cockpit view of their own car.
2. Record the lap with OBS: 2560x1440, 60 fps, from 5 seconds before the line to 3 seconds after the lap finish. OBS saves to the folder from the settings.
3. Tell the file name and how much to trim at the start if the camera glitched.

How to find the lap: take its number and time from the start from the telemetry (`race.trace`, columns `t`, `lap`) and tell the user before recording.

## Steps

1. Race recording: `py -3 tools/race_report.py --list`. Take the recording from this PC, its end matches the time of the `.frr` file.
   `races/` sometimes holds a second player's recording of the same race with the same session id, and the script takes the car's lap from the recording.
   Check that the best lap in `report.md` matches the one the user is talking about.
2. Clip: `ffprobe`, it must be 2560x1440 and 60 fps, otherwise ask for a re-recording: the panels are fitted to the replay plates at this size.
3. The lap start frame. First roughly, with a contact sheet of the plate (`crop=440:170:40:40`, `fps=2`, `tile`), then precisely:

   ```
   ffmpeg -i CLIP -vf "select='between(n,A,B)',crop=70:46:218:162,scale=140:-1,tile=15x4" -fps_mode passthrough -frames:v 1 strip.png
   ```

   Lap start: frame `n` where the lap number changes, the digits are usually blended in it. `--lap-start` = `n / 60`.
   Find frame `m` where the number changes at the end of the lap the same way.
4. Sync check before the build:
   - `(m - n) / 60` equals the lap time to within 2 frames. If not, it's the wrong lap or the replay was not at normal speed;
   - one gear change from `race.trace` (`gear`) is visible on the steering wheel in the computed frame (`crop=160:64:1262:1124`).
5. Build:

   ```
   py -3 tools/lap_video.py races\RECORDING.f1rec.gz --clip CLIP --lap-start N --trim T --first FIRST --last LAST --song SONG
   ```

   Output: `races/reports/<recording>/fastest_lap.mp4`. A failure writing `bg.png` happened once, re-running helped.
6. Check the result:
   - `ffprobe`: profile `High`, `yuv420p`. The Windows player does not open 4:4:4, error 0x80004005;
   - look at frames: mid-lap, the moment after the finish, the title card. The panels cover the `RACE`, `REPLAY` plates and the Esc and F10 hints;
   - `volumedetect`: the engine averages about −19 dB, the song about −11 dB. Another OBS recording may have a different level, then adjust `GAME_GAIN_DB`.
7. In the reply: the path to the file and what was checked. The agent cannot listen to the sound or see the motion, say so.

## Video format

1. The lap from the cockpit with game sound. Top left the lap number and timer, top right the sectors as they are completed, bottom right speed, gear, throttle and brake.
2. At the finish the timer turns purple, a caption replaces the lap number. 2 more seconds of video.
3. A smooth 0.8 s transition, the game sound fades out, the song plays from the start of the transition.
4. Title card: flag, Grand Prix, first name outlined, last name large, pink line, team, caption, time outlined. Elements appear one by one, a fade to black at the end.
