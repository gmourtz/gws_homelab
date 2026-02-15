# src/authenticate.py

import os
import google_auth_oauthlib.flow
import googleapiclient.discovery

# This is the scope that allows for reading your YouTube playlists.
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"

# This file should be downloaded from your Google Cloud project.
CLIENT_SECRETS_FILE = "client_secret.json"
TOKEN_FILE = "token.json"


def get_authenticated_service():
    """
    Performs the OAuth 2.0 flow to get a token and returns an authenticated API client.
    """
    if not os.path.exists(CLIENT_SECRETS_FILE):
        print("=" * 80)
        print("ERROR: Cannot find client secrets file.")
        print(f"Please download your OAuth 2.0 client ID file from the Google Cloud Console")
        print(f"and save it as '{CLIENT_SECRETS_FILE}' in the same directory as this script.")
        print("=" * 80)
        return None

    flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, SCOPES
    )
    
    # The `run_local_server` method opens a browser window for the user to
    # authorize the application.
    credentials = flow.run_local_server(port=0)
    
    # Save the credentials for the next run
    with open(TOKEN_FILE, "w") as token:
        token.write(credentials.to_json())
    
    print(f"\nAuthentication successful. Token saved to '{TOKEN_FILE}'.")
    print("You can now run the main application.")
    
    return googleapiclient.discovery.build(
        API_SERVICE_NAME, API_VERSION, credentials=credentials
    )


if __name__ == "__main__":
    print("Starting the authentication process...")
    print("You will be prompted to log in to your Google account and grant permissions.")
    get_authenticated_service()
