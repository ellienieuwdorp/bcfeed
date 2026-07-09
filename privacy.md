## Privacy & Data Use

This application runs on your local machine. 

- The application does not collect, transmit, store, or share your data with the author or any third party.

- Gmail API access is performed locally using OAuth credentials that you create and control in your own Google Cloud project. The application requests only the read-only Gmail scope (`https://www.googleapis.com/auth/gmail.readonly`), so it can never send, modify, or delete your mail.

- IMAP access is performed locally using server credentials that you create and control with your email provider. The application opens your mailbox in read-only mode, so it can only read messages — it never marks them read, moves, modifies, or deletes them.

- Sensitive Gmail OAuth material, Gmail tokens, and IMAP passwords managed by bcfeed are stored in your system keychain (your Mac's Keychain). bcfeed only ever reads your mail; it does not store copies of your mail credentials anywhere else.

- No analytics, telemetry, usage tracking, or remote logging is included; any email data cached by the application remains entirely on your local machine.

- The author of this software never has access to your OAuth/IMAP credentials, access tokens, refresh tokens, or email data.

You may revoke the application’s access to your Google account at any time via:
https://myaccount.google.com/permissions

This software is provided as-is for personal use. You are responsible for complying with your email provider’s terms when creating and using OAuth credentials, IMAP passwords, or app-specific passwords.
