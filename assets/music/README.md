# Music library for reels

Drop royalty-free tracks (mp3/wav/m4a/ogg/flac) into a mood folder:

| Folder | Picked when the prompt mentions |
|---|---|
| `epic/` | battle, fight, hero, warrior, action, power, dragon... |
| `calm/` | sunset, beach, ocean, forest, nature, morning, mountain... |
| `happy/` | party, festival, dance, celebration, birthday, joy... |
| `emotional/` | love, romantic, wedding, memory, rain, family... |
| `devotional/` | temple, puja, prayer, krishna, shiva, diwali, saree... |

- No mood keyword matches -> any track. Empty library -> reel stays silent (same as before).
- Each reel gets the track looped/trimmed to its length, 1 s fade in/out, normalised to about -14 LUFS; a voiceover (if on) ducks the music.
- Free sources: Pixabay Music (pixabay.com/music), YouTube Audio Library (studio.youtube.com -> Audio library). Check each track's licence for the platform you post on. Files are not committed to git.
- Choose a track yourself with `--music path/to/song.mp3`; disable with `--music none`; default is `auto`.
