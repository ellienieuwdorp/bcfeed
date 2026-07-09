# bcfeed — installation & setup

How to install and run **bcfeed**. For what it does and how to use it, see [README.md](README.md).

## Installation

### For power users (Homebrew already installed)

To install, open a Terminal and type the following:
`brew tap keinobjekt/bcfeed`
`brew install bcfeed`

Then to run simply type:
`bcfeed`

This will launch the server from the Terminal and open the dashboard in your web browser. You must keep the Terminal window open in the background in order to use **bcfeed**.


### For beginners

1) Download and install **Homebrew**: https://brew.sh
2) Use Homebrew to install **bcfeed**:
   - Open a Terminal window.
   - Type `brew tap keinobjekt/bcfeed` and hit enter.
   - Type `brew install bcfeed` and hit enter. This will begin the installation. 

To run **bcfeed**:
   - Type `bcfeed` into Terminal and hit enter
   - This will launch the server from the Terminal and open the dashboard in your web browser. 

You only need to install **bcfeed** once. 
You must keep the Terminal window open in the background in order to use **bcfeed**.


### Running from Python source (developers and advanced users)

If you're familiar with Python and CLI tools, you can create a virtual environment, install the dependencies and run the script from the CLI:

- Download **bcfeed** source code
- Ensure Python 3.11 or newer is installed and selected as the local python version
- In the project directory, run `virtualenv .venv`
- Run `source .venv/bin/activate`
- Download dependencies: `pip install -r requirements.txt`
- Run `python3 bcfeed.py`

This will launch the server from the CLI and open the dashboard in your web browser.

You must keep the CLI process running in order to use **bcfeed**.

## Connect your email

Open **Settings → Email connection** after launching the app, then choose **How bcfeed reads your email**. **bcfeed** supports two ways to connect:

- **Google sign-in (Gmail)**: recommended if your Bandcamp mail lives in Gmail and you do not mind creating your own Google access file. Access is read-only: the app can only read messages, never send, modify, or delete them.
- **Mail server (IMAP)**: works with most email services, including Gmail, iCloud, Outlook, Fastmail, and other IMAP-compatible mailboxes.

Your Gmail sign-in and IMAP password are kept in your Mac's Keychain. The rest of your mail settings are stored locally in the app data directory.

Setup guides:
- Google sign-in (Gmail): [GMAIL_SETUP.md](GMAIL_SETUP.md)
- Mail server (IMAP): [IMAP_SETUP.md](IMAP_SETUP.md)
