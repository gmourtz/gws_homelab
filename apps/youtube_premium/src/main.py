# src/main.py

import os
import time
import subprocess
from pathlib import Path
import glob
import json

import google.oauth2.credentials
import googleapiclient.discovery

# --- Configuration ---
# Read from environment variables, with fallbacks
VIDEOS_DIR = Path(os.getenv("VIDEOS_DIR", "/videos"))
DOWNLOAD_PLAYLIST_ID = os.getenv("PLAYLIST_ID", "PLZskJ7oz20HcggNZahqoXceJ5J0T8TuND") # Default to your existing playlist
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "600")) # Default to 10 minutes
TOKEN_FILE = os.getenv("TOKEN_FILE", "token.json") # Default token file name
AUDIO_ONLY = os.getenv("AUDIO_ONLY", "false").lower() == "true" # New: Extract audio only
AUDIO_FORMAT = os.getenv("AUDIO_FORMAT", "m4a") # New: Audio format if AUDIO_ONLY is true
STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", ".downloaded_videos.log") # New: Configurable state file name

STATE_FILE = VIDEOS_DIR / STATE_FILE_NAME

# --- Debugging ---
print(f"DEBUG: VIDEOS_DIR: {VIDEOS_DIR}")
print(f"DEBUG: DOWNLOAD_PLAYLIST_ID: {DOWNLOAD_PLAYLIST_ID}")
print(f"DEBUG: AUDIO_ONLY (env): {os.getenv('AUDIO_ONLY')}")
print(f"DEBUG: AUDIO_ONLY (parsed): {AUDIO_ONLY}")
print(f"DEBUG: AUDIO_FORMAT (env): {os.getenv('AUDIO_FORMAT')}")
print(f"DEBUG: AUDIO_FORMAT (parsed): {AUDIO_FORMAT}")
print(f"DEBUG: STATE_FILE_NAME (env): {os.getenv('STATE_FILE_NAME')}")
print(f"DEBUG: STATE_FILE: {STATE_FILE}")

# --- Constants ---
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"


def get_youtube_service():
    """
    Initializes and returns the YouTube API service client using saved credentials.
    """
    print("Initializing YouTube service...")
    if not os.path.exists(TOKEN_FILE):
        print(f"Error: Token file '{TOKEN_FILE}' not found.")
        print("Please run 'python src/authenticate.py' first to generate it.")
        return None

    try:
        credentials = google.oauth2.credentials.Credentials.from_authorized_user_file(TOKEN_FILE)
        service = googleapiclient.discovery.build(
            API_SERVICE_NAME, API_VERSION, credentials=credentials
        )
        print("YouTube service initialized successfully.")
        return service
    except Exception as e:
        print(f"Error initializing YouTube service: {e}")
        return None


def get_playlist_videos(service):
    """
    Fetches the list of all video IDs from the target playlist.
    """
    print(f"Fetching playlist ID: {DOWNLOAD_PLAYLIST_ID}...")
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

        print(f"Found {len(video_ids)} total videos in playlist.")
        return video_ids
    except Exception as e:
        print(f"An error occurred while fetching the playlist: {e}")
        return []


def load_downloaded_videos():
    """Loads the set of already downloaded video IDs from the state file."""
    if not STATE_FILE.exists():
        return set()
    with open(STATE_FILE, "r") as f:
        return {line.strip() for line in f}


def save_downloaded_videos(video_ids):
    """Saves the set of video IDs to the state file."""
    with open(STATE_FILE, "w") as f:
        for video_id in video_ids:
            f.write(f"{video_id}\n")


def download_video(video_id):
    """
    Downloads the given video ID to the videos directory using yt-dlp.
    """
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"Downloading video: {video_url}")
    try:
        cmd = [
            "yt-dlp",
            "-o", f"{VIDEOS_DIR}/%(title)s [%(id)s].%(ext)s",
            video_url,
        ]
        if AUDIO_ONLY:
            cmd.extend(["--extract-audio", "--audio-format", AUDIO_FORMAT, "--audio-quality", "0"]) # Added --audio-quality 0
        else:
            # Download best quality video and audio and merge them into a single mp4 file.
            cmd.extend(["-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best", "--merge-output-format", "mp4"])

        print(f"DEBUG: Final yt-dlp command: {cmd}") # Debug print
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"Successfully downloaded video ID: {video_id}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error downloading video ID {video_id}.")
        print(f"Stderr: {e.stderr}")
        return False





def delete_video_file(video_id):
    """Deletes the video file."""
    print(f"Attempting to delete files for video ID: {video_id}")
    
    # Find the video file that contains `[video_id]` in its name
    video_path = next(VIDEOS_DIR.glob(f"*[[]*{video_id}[]].*"), None)

    if not video_path:
        print(f"Warning: No file found for video ID {video_id} in {VIDEOS_DIR}")
        return False

    try:
        video_path.unlink()
        print(f"Successfully deleted file: {video_path}")
        return True
    except OSError as e:
        print(f"Error deleting file {video_path}: {e}")
        return False


def main():
    """Main application loop."""
    print(f"Starting YouTube Playlist downloader for playlist {DOWNLOAD_PLAYLIST_ID} into {VIDEOS_DIR}...")
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True) # Ensure parent directories exist

    youtube_service = get_youtube_service()
    if not youtube_service:
        return

    while True:
        print(f"\n--- Checking for playlist changes in {DOWNLOAD_PLAYLIST_ID} ---")
        
        # 1. Get current state from YouTube and local state
        playlist_ids = set(get_playlist_videos(youtube_service))
        downloaded_ids = load_downloaded_videos()
        print(f"Found {len(downloaded_ids)} videos recorded as downloaded.")

        # 2. Identify and delete videos no longer in the playlist
        videos_to_delete = downloaded_ids - playlist_ids
        if videos_to_delete:
            print(f"Found {len(videos_to_delete)} videos to delete.")
            deleted_count = 0
            for video_id in videos_to_delete:
                if delete_video_file(video_id):
                    downloaded_ids.remove(video_id)
                    deleted_count += 1
            
            if deleted_count > 0:
                # Update the state file immediately after deletion
                save_downloaded_videos(downloaded_ids)
        else:
            print("No videos to delete.")

        # 3. Identify and download new videos
        videos_to_download = playlist_ids - downloaded_ids
        if not videos_to_download:
            print("No new videos found to download.")
        else:
            print(f"Found {len(videos_to_download)} new videos to download.")
            for video_id in videos_to_download:
                if download_video(video_id):
                    downloaded_ids.add(video_id)
            # Update the state file after all downloads
            save_downloaded_videos(downloaded_ids)

        print(f"Sleeping for {POLL_INTERVAL} seconds...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
