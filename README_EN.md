\# 🎬 TubeRevival



\*\*Reviving YouTube 2.2.0 on iPhone 4S with iOS 6\*\*



\---



\## ⚠️ Important Warning



\- \*\*I (MSWorks) am not responsible\*\* for any device issues, data loss, or information leaks. Everything you do is at your own risk.

\- \*\*Google API Key\*\* – you create it yourself. Instructions below.



\---



\## 🔑 How to get a Google API Key (required!)



1\. Go to \[Google Cloud Console](https://console.cloud.google.com/).

2\. Create a new project (or select an existing one).

3\. Enable \*\*YouTube Data API v3\*\*.

4\. Create an \*\*API Key\*\* (restrict it to your PC's IP address for security!).

5\. Copy the key.



\---



\## ⚙️ Installing and Configuring the Server on a PC



1\. \*\*Download the repository\*\* or copy the files to your computer.

2\. \*\*Install the dependencies\*\* (in one command):

```bash

pip install fastapi uvicorn httpx pillow python-dotenv

