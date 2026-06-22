# YouTube Premium Impersonator

This project automatically downloads videos from a specified YouTube playlist and stores them locally with metadata and thumbnails for a rich media experience in Jellyfin.

It runs as a self-contained Docker application, designed to be simple, private, and easy to maintain on a home server or any machine running Docker.

## Features

- **Automatic Downloads:** Periodically checks your YouTube playlist and downloads any new videos.
- **Automatic Deletion:** Removes videos and their associated metadata from your local storage when they are removed from the playlist.
- **Age-Based Retention (optional):** Set `RETENTION_DAYS` to automatically delete downloads older than N days (e.g. a rolling one-month podcast window). Expired files are removed but not re-downloaded while they remain in the playlist.
- **Metadata and Thumbnails:** Downloads video thumbnails, embeds metadata into the video files, and creates NFO files for full compatibility with media servers like Jellyfin.
- **Local Storage:** Keeps all your videos on your own hardware.
- **Stateful:** Remembers which videos have been downloaded to prevent duplicates.
- **Audio-Only Downloads:** Option to download only the audio track of videos.
- **Simple Deployment:** Uses Docker Compose for a one-command setup.

## How It Works

1.  You add or remove a video from your specified YouTube playlist.
2.  The application, running in a Docker container, periodically checks the playlist using the YouTube API.
3.  If it finds a new video, it uses `yt-dlp` to download the video, its thumbnail, and a metadata information file. It then creates a Jellyfin-compatible NFO file.
4.  If it finds a video that is no longer in the playlist, it deletes the video and all associated metadata files.

## Requirements

- **Docker and Docker Compose:** The application is designed to run with Docker. [Install Docker Desktop](https://www.docker.com/products/docker-desktop/) (which includes Compose).
- **Google Account:** To access your YouTube playlist.
- **YouTube Data API Credentials:** You need to get API credentials from the Google Cloud Console to allow the application to access your playlist data.

## Configuration

The application can be configured using the following environment variables, which can be set in your `docker-compose.yml` file or directly in your environment before running the Docker container.

-   `PLAYLIST_ID`: **(Required)** The ID of the YouTube playlist you want to monitor. You can find this in the URL of your YouTube playlist (e.g., `https://www.youtube.com/playlist?list=YOUR_PLAYLIST_ID`).
-   `VIDEOS_DIR`: (Optional) The directory inside the container where videos will be stored. Defaults to `/videos`. This should typically be mapped to a local volume in your `docker-compose.yml`.
-   `POLL_INTERVAL`: (Optional) The interval (in seconds) between checks for new videos in the playlist. Defaults to `600` seconds (10 minutes).
-   `TOKEN_FILE`: (Optional) The name of the file where your YouTube API authentication token is stored. Defaults to `token.json`.
-   `AUDIO_ONLY`: (Optional) Set to `true` to download only the audio track of videos. Defaults to `false`.
-   `AUDIO_FORMAT`: (Optional) If `AUDIO_ONLY` is `true`, this specifies the audio format to download. Defaults to `m4a`.
-   `STATE_FILE_NAME`: (Optional) The name of the file used to store the IDs of already downloaded videos. Defaults to `.downloaded_videos.log`.
-   `RETENTION_DAYS`: (Optional) Delete downloaded files older than this many days (measured from download time). Defaults to `0`, which **disables** retention (files are only removed when pulled from the playlist). Expired files are deleted from disk but their IDs stay in the state file, so they are not re-downloaded while still present in the playlist.

## Setup and Configuration

Follow these steps to get the application running.

### 1. Get YouTube API Credentials

You need to authorize the application to access your YouTube account.

1.  **Enable the API:** Go to the [Google Cloud Console](https://console.cloud.google.com/) and enable the **YouTube Data API v3**.
2.  **Create Credentials:** Go to the **Credentials** page, click **+ CREATE CREDENTIALS**, and select **OAuth client ID**.
3.  **Configure Consent Screen:** If prompted, choose **External** user type, and provide a name for the app (e.g., "YouTube Downloader") and your email. You do not need to submit it for verification.
4.  **Set Application Type:** Choose **Desktop app** as the application type.
5.  **Download Credentials:** After creating the client ID, a window will pop up. Click the **DOWNLOAD JSON** button.
6.  **Rename and Place the File:** Rename the downloaded file to `client_secret.json` and place it in the root of this project directory.

### 2. First-Time Authentication

Now, you need to generate a token that the application will use to make authenticated API calls.

1.  **Install Dependencies Locally:** You only need to do this once to generate the token.
    ```bash
    pip install -r requirements.txt
    ```
2.  **Run the Authentication Script:**
    ```bash
    python3 src/authenticate.py
    ```
3.  **Authorize Access:** Your web browser will open and ask you to log in to your Google account and grant the application permission.
4.  **Token Generation:** After you approve, a `token.json` file will be created in the project directory. This file stores the authorization token.

**Important:** Keep the `client_secret.json` and `token.json` files secure. They are included in the `.gitignore` file to prevent you from accidentally committing them.

## Running the Application

With the `docker-compose.yml` file, running the application is a single command.

1.  **Update `docker-compose.yml`:** Open the `docker-compose.yml` file and set the `PLAYLIST_ID` environment variable.
2.  **Start the Service:** From the root of the project directory, run:
    ```bash
    docker-compose up -d --build
    ```
    - `up`: Creates and starts the container.
    - `-d`: Runs the container in detached mode (in the background).
    - `--build`: Rebuilds the image if there have been any changes to the `Dockerfile` or source code.

3.  **Verify It's Running:** You can check the logs to see the application's output.
    ```bash
    docker-compose logs -f
    ```
    You should see messages indicating that the service has started and is checking for videos.

## Accessing Your Videos

Your downloaded videos and music will be in the `videos` and `music` directories in the project folder. You can then add these directories to your media server of choice (like Jellyfin, Plex, etc.).

Jellyfin will automatically pick up the thumbnails and NFO files, giving you a rich media library.

For secure remote access, it is highly recommended to use a private network overlay like [Tailscale](https://tailscale.com/) and access your files directly.

## Stopping the Application

To stop the application and shut down the container, run:
```bash
docker-compose down
```
This will stop and remove the container but will not delete your media files or your `token.json` file.


