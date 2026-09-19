"""Connect Now Playing to your Spotify account. Run it once on a computer with a browser:

    python spotify_login.py YOUR_CLIENT_ID --rack rackticker.local:8081

It opens Spotify's sign-in page, catches the answer on this computer
(http://127.0.0.1:8888/callback, which your Spotify app must list as a Redirect
URI), and saves the login to your RackTicker. Without --rack it prints the two
values to paste into the plugin's settings. Standard library only; no client
secret (PKCE), and the only permission asked is to see what is playing.
"""
import argparse
import base64
import hashlib
import http.server
import json
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser

REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = "user-read-currently-playing user-read-playback-state"


def login(client_id):
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state})
    answer = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            answer.update({key: values[0] for key, values in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Done. You can close this tab and go back to the terminal.".encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 8888), Callback)
    print("Opening Spotify in your browser. If it does not open, visit:\n" + url)
    webbrowser.open(url)
    while "code" not in answer and "error" not in answer:
        server.handle_request()
    server.server_close()
    if answer.get("error") or answer.get("state") != state:
        sys.exit(f"Spotify said no: {answer.get('error', 'the reply did not match this login')}")
    form = urllib.parse.urlencode({"grant_type": "authorization_code", "code": answer["code"],
                                   "redirect_uri": REDIRECT, "client_id": client_id,
                                   "code_verifier": verifier}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://accounts.spotify.com/api/token", data=form)) as reply:
        return json.load(reply)["refresh_token"]


def save(rack, client_id, token):
    base = f"http://{rack}/api/config"
    config = json.load(urllib.request.urlopen(base))
    if "now_playing" not in config.get("plugins", {}):
        sys.exit("Install Now playing on the rack first (Plugins → Community), then run this again")
    config["plugins"]["now_playing"].update({"source": "spotify", "spotify_client_id": client_id,
                                            "spotify_refresh_token": token})
    request = urllib.request.Request(base, data=json.dumps(config).encode(), method="PUT",
                                     headers={"Content-Type": "application/json", "X-RackTicker": "1"})
    urllib.request.urlopen(request)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("client_id", help="Client ID of your app at developer.spotify.com/dashboard")
    parser.add_argument("--rack", help="Your RackTicker's address, e.g. rackticker.local:8081, to save it there")
    args = parser.parse_args()
    refresh = login(args.client_id.strip())
    if args.rack:
        save(args.rack, args.client_id.strip(), refresh)
        print("Saved. Play something on Spotify and it appears on the rack within a few seconds.")
    else:
        print(f"\nSpotify client ID: {args.client_id.strip()}\nSpotify login:     {refresh}\n"
              "Paste both into Now playing's settings.")
