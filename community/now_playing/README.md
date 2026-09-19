# Now playing

The song you are listening to, drawn like an old car stereo: album art, the title
and artist scrolling through, track number and time, and a segmented spectrum
analyser with peak caps that takes over the whole display every few seconds.

Neither Spotify nor Home Assistant hands out the audio itself, so the analyser is
a drummer, not a microphone: a beat at a tempo picked for each song, following
the song's real position and stopping when you pause.

## Spotify

1. At [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
   create an app (any name) with the Web API, and add the Redirect URI
   `http://127.0.0.1:8888/callback`. Copy its **Client ID**.
2. Install **Now playing** from Plugins → Community.
3. On a computer with a browser, from this folder:

   ```sh
   python spotify_login.py YOUR_CLIENT_ID --rack rackticker.local:8081
   ```

   Sign in, allow it, and the login is saved to your RackTicker. (Without
   `--rack` it prints the two values to paste into the plugin's settings.)

It asks only to see what is playing. No client secret is used or stored. Spotify
replaces the login on every refresh; the plugin keeps the newest one in its own
data folder, and the settings page shows dots instead of it.

## Home Assistant

Anything Home Assistant can see playing works: Spotify, Sonos, an Apple TV, an
Echo. Set **Source** to Home Assistant, the address (for example
`http://homeassistant.local:8123`), and a long-lived access token (your profile →
Security). Leave **Player** empty to follow whichever one is playing.

Lyrics come from [LRCLIB](https://lrclib.net), the free open lyrics database, when a song
has time-synced lyrics there: the line being sung runs under the title, and the
analyser takes over between lines. Switch them off in the settings.

The screen skips itself when nothing has played for two minutes.
