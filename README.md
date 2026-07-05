# bcfeed

## Introduction

**bcfeed** is a local macOS app that turns your Bandcamp "New release from..." notification emails into a browsable dashboard of releases.

It works by searching your configured email account within a given date range for Bandcamp release notification emails, then saving the release details locally so you can sort, filter, and preview them.

You can connect either:

- a Gmail account using the Gmail API and your own OAuth client
- any IMAP-compatible mailbox by entering the server details in the Settings panel


## Setup

See [SETUP.md](SETUP.md) to install and run **bcfeed**. Once it's running, set up an email provider:

- Gmail API (OAuth): [GMAIL_SETUP.md](GMAIL_SETUP.md)
- IMAP: [IMAP_SETUP.md](IMAP_SETUP.md)


## Workflow

**A typical workflow would be:**

1) **Select a date range to populate**, e.g. the whole of last December. 
2) Click **"Populate release list"**. This searches your configured provider for Bandcamp release notifications within the specified date range and saves the results locally.
3) Now you can do one of three things:
  - **Browse straight away** - this works! However, this is likely to be slow – the data for each release needs to be loaded from each Bandcamp page individually, which takes a few seconds any time you click on a release. For a more enjoyable UX, you can
  - Click **"Preload release data"** and then browse: this preloads the release info and BC player widgets for all releases in the selected date range for faster browsing, but may take a while for larger date ranges. Or you can
  - **"Star" the releases you're interested in, then filter and browse the starred releases using the "Starred" button at the top right**. Starring a release triggers a preload behind the scenes, so by the time you click "Starred", the releases should already be loaded.
4) If you like, once you've browsed that date range, you can mark all the releases as "Seen" (in the left panel). 


## Notes

Once you've fetched (or browsed) a date range from your mailbox once, you don't have to do it again. Each line in the saved list corresponds to an *email* in your inbox. So if you've already fetched a date range in the past, that's all the releases (emails) you'll ever see in that date range.

Releases with preloaded release data and player widgets are marked with a blue "CACHED" badge.

The Settings panel at the top right allows you to choose a provider, load or clear Gmail credentials, configure IMAP host/user/folder settings, and reset the cache.

Sensitive Gmail OAuth material, Gmail tokens, and IMAP passwords managed by **bcfeed** are stored securely in your system keychain.


## Performance

### Why are releases loading so slowly?

Make sure to read the bit above about pre-loading releases.

Typically it's pretty quick to fetch releases from Gmail, a few hundred emails should take a few seconds. IMAP accounts can take a bit longer and be provider dependant, though ~100 emails should typically not take much longer than a minute. However, Bandcamp release notification emails only contain basic metadata (artist, title, label/page, Bandcamp URL). Fetching the Bandcamp player widget and release info requires scraping the Bandcamp page for each release, which is much slower (a few seconds per release). Naturally, you are also at the mercy of the Bandcamp servers at any given moment – it's not uncommon for them to slow to a crawl.

In general it's better to pre-load the releases, either using the "Preload" button on the left side or by starring them.

The good news is that **bcfeed** saves the release list, release info and Bandcamp player widgets locally and these persist across **bcfeed** sessions, so you only need to preload once for any given release.


## Requirements

**bcfeed** has mainly been tested on recent macOS and Chrome. It probably works on other macOS versions. It may or may not work on other browsers. It probably won't work on Windows, but feel free to try.

**bcfeed** supports:
- Gmail accounts via the Gmail API and your own OAuth client
- IMAP-compatible accounts via the Settings panel

Some IMAP providers require IMAP to be enabled and/or an app-specific password. Secure credential storage also requires a working system keychain backend (available by default on macOS).


## Privacy

**bcfeed** runs entirely on your local machine. It collects no analytics or telemetry, and the author never has access to your credentials or email data. For the full details, see [privacy.md](privacy.md).


## License

**bcfeed** is released under the MIT License. See [LICENSE](LICENSE).
