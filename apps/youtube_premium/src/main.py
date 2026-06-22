# src/main.py

import os
import sys
import time
import subprocess
import logging
from pathlib import Path

import google.oauth2.credentials
import google.auth.transport.requests
import googleapiclient.discovery

from retention import prune_old_files

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# --- Configuration ---
VIDEOS_DIR = Path(os.getenv("VIDEOS_DIR", "/videos"))
DOWNLOAD_PLAYLIST_ID = os.getenv("PLAYLIST_ID", "PLZskJ7oz20HcggNZahqoXceJ5J0T8TuND")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "600"))
TOKEN_FILE = os.getenv("TOKEN_FILE", "token.json")
AUDIO_ONLY = os.getenv("AUDIO_ONLY", "false").lower() == "true"
AUDIO_FORMAT = os.getenv("AUDIO_FORMAT", "m4a")
STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", ".downloaded_videos.log")
# Age-based retention: delete downloaded files older than this many days.
# 0 (default) disables retention — files are only removed when pulled from the
# playlist. Expired files are deleted but their IDs stay in the state file, so
# they are NOT re-downloaded while still present in the playlist.
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "0"))

STATE_FILE = VIDEOS_DIR / STATE_FILE_NAME

# --- Constants ---
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"


def _save_credentials(credentials):
    """Persist credentials (including refreshed tokens) back to disk."""
    try:
        with open(TOKEN_FILE, "w") as f:
            f.write(credentials.to_json())
        log.debug("Saved refreshed credentials to %s", TOKEN_FILE)
    except OSError as e:
        log.warning("Could not save refreshed token: %s", e)


def get_youtube_service():
    """
    Initializes and returns the YouTube API service client using saved credentials.
    Automatically refreshes expired tokens and persists the new token to disk.
    Returns (service, credentials) tuple so the caller can persist tokens later.
    """
    log.info("Initializing YouTube service...")
    if not os.path.exists(TOKEN_FILE):
        log.error("Token file '%s' not found. Run 'python src/authenticate.py' first.", TOKEN_FILE)
        return None, None

    try:
        credentials = google.oauth2.credentials.Credentials.from_authorized_user_file(TOKEN_FILE)

        # Proactively refresh if expired
        if credentials.expired and credentials.refresh_token:
            log.info("Access token expired, refreshing...")
            credentials.refresh(google.auth.transport.requests.Request())
            _save_credentials(credentials)

        service = googleapiclient.discovery.build(
            API_SERVICE_NAME, API_VERSION, credentials=credentials
        )
        log.info("YouTube service initialized successfully.")
        return service, credentials
    except Exception as e:
        log.error("Error initializing YouTube service: %s", e)
        return None, None


def get_playlist_videos(service):
    """Fetches the list of all video IDs from the target playlist."""
    log.info("Fetching playlist ID: %s", DOWNLOAD_PLAYLIST_ID)
    video_ids = []
    next_page_token = None

    try:
        while True:
            request = service.playlistItems().list(
                part="contentDetails",
                playlistId=DOWNLOAD_PLAYLIST_ID,
                maxResults=50,
                pageToken=next_page_token,
            )
            response = request.execute()

            for item in response.get("items", []):
                video_id = item.get("contentDetails", {}).get("videoId")
                if video_id:
                    video_ids.append(video_id)

            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break

        log.info("Found %d total videos in playlist.", len(video_ids))
        return video_ids
    except Exception as e:
        log.error("Error fetching playlist: %s", e)
        return []


def load_downloaded_videos():
    """Loads the set of already downloaded video IDs from the state file."""
    if not STATE_FILE.exists():
        return set()
    with open(STATE_FILE, "r") as f:
        return {line.strip() for line in f if line.strip()}


def save_downloaded_videos(video_ids):
    """Saves the set of video IDs to the state file."""
    with open(STATE_FILE, "w") as f:
        for video_id in video_ids:
            f.write(f"{video_id}\n")


def build_download_command(video_url):
    """Builds the yt-dlp command for the given URL based on the configured mode.

    ``--no-mtime`` keeps the file's mtime at download time (instead of the video's
    upload date), which is what age-based retention measures against.
    """
    cmd = [
        "yt-dlp",
        "--no-mtime",
        "-o", f"{VIDEOS_DIR}/%(title)s [%(id)s].%(ext)s",
        video_url,
    ]
    if AUDIO_ONLY:
        cmd.extend([
            "--extract-audio",
            "--audio-format", AUDIO_FORMAT,
            "--audio-quality", "0",
            "--embed-metadata",
            "--embed-thumbnail",
            "--parse-metadata", "uploader:%(artist)s",
            "--parse-metadata", "title:%(title)s",
        ])
    else:
        cmd.extend(["-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best", "--merge-output-format", "mp4"])
    return cmd


def download_video(video_id):
    """Downloads the given video ID to the videos directory using yt-dlp."""
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    log.info("Downloading: %s", video_url)
    try:
        subprocess.run(build_download_command(video_url), check=True)
        log.info("Downloaded video ID: %s", video_id)
        return True
    except subprocess.CalledProcessError as e:
        log.error("Failed to download %s: %s", video_id, e.stderr)
        return False


def delete_video_file(video_id):
    """Deletes the video file matching the given video ID."""
    log.info("Deleting files for video ID: %s", video_id)
    video_path = next(VIDEOS_DIR.glob(f"*[[]*{video_id}[]].*"), None)

    if not video_path:
        # Already absent (e.g. removed by retention) — treat as success so the
        # caller drops the ID from state instead of retrying every poll.
        log.debug("No file found for video ID %s in %s (already absent)", video_id, VIDEOS_DIR)
        return True

    try:
        video_path.unlink()
        log.info("Deleted: %s", video_path)
        return True
    except OSError as e:
        log.error("Error deleting %s: %s", video_path, e)
        return False


def main():
    """Main application loop."""
    log.info("Starting YouTube downloader — playlist=%s dir=%s audio=%s retention_days=%s",
             DOWNLOAD_PLAYLIST_ID, VIDEOS_DIR, AUDIO_ONLY, RETENTION_DAYS or "disabled")
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    youtube_service, credentials = get_youtube_service()
    if not youtube_service:
        sys.exit(1)

    while True:
        log.info("--- Checking for playlist changes ---")

        playlist_ids = set(get_playlist_videos(youtube_service))
        if not playlist_ids:
            log.warning("Got empty playlist — API issue? Reinitializing service...")
            youtube_service, credentials = get_youtube_service()
            if not youtube_service:
                log.error("Failed to reinitialize. Retrying in %d seconds.", POLL_INTERVAL)
            time.sleep(POLL_INTERVAL)
            continue

        downloaded_ids = load_downloaded_videos()
        log.info("Recorded: %d downloaded, %d in playlist.", len(downloaded_ids), len(playlist_ids))

        # Delete videos removed from playlist
        to_delete = downloaded_ids - playlist_ids
        if to_delete:
            log.info("Deleting %d removed videos.", len(to_delete))
            for vid in to_delete:
                if delete_video_file(vid):
                    downloaded_ids.discard(vid)
            save_downloaded_videos(downloaded_ids)

        # Download new videos
        to_download = playlist_ids - downloaded_ids
        if to_download:
            log.info("Downloading %d new videos.", len(to_download))
            for vid in to_download:
                if download_video(vid):
                    downloaded_ids.add(vid)
            save_downloaded_videos(downloaded_ids)
        else:
            log.info("Everything up to date.")

        # Enforce age-based retention (no-op unless RETENTION_DAYS > 0). Expired
        # files are removed from disk but their IDs remain in the state file, so
        # they are not re-downloaded while still present in the playlist.
        prune_old_files(VIDEOS_DIR, RETENTION_DAYS)

        # Persist refreshed credentials if the API client auto-refreshed them
        if credentials and credentials.token:
            _save_credentials(credentials)

        log.info("Sleeping %d seconds...", POLL_INTERVAL)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
