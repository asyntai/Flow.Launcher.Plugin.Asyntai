# Asyntai AI Chatbot for Flow Launcher

[Asyntai](https://asyntai.com) is an AI chat agent for your website. It answers your visitors from your own content, collects leads and hands chats over to your team. This plugin brings that work into [Flow Launcher](https://www.flowlauncher.com), so you read chats and leads, and test your chatbot, without opening the browser.

## Commands

Type `asy` and a space in Flow Launcher.

- `asy chats`: the newest chats on your website, with the time, the number of messages, the domain and the country. Press Enter to read the whole chat, message by message.
- `asy leads`: every visitor who left an email address or a phone number, with the page they were on. Press Enter to read their chat.
- `asy tickets`: your support tickets, with the number, the status and the visitor.
- `asy ask <question>`: press Enter and read the answer your chatbot gives your visitors. Ask a follow-up and it keeps the conversation.
- `asy <anything>`: any other text is a question for your chatbot.

Add a word after `chats`, `leads` or `tickets` to filter the list, for example `asy leads gmail`.

Press Shift+Enter on a chat, lead or ticket to open it in Asyntai, open the page the visitor was on, or copy the email, phone or ticket number. On an answer, Shift+Enter copies it or opens its links.

## Setup

1. Install the plugin from the Flow Launcher plugin store: type `pm install Asyntai AI Chatbot`.
2. Sign in to Asyntai, open **Settings**, then **API**, and copy your API key.
3. Open Flow Launcher settings, **Plugins**, **Asyntai AI Chatbot**, and paste the key.

The plugin is free. It uses the Asyntai API, which needs an Asyntai account on the Starter plan or higher.

## Requirements

- Flow Launcher 2.1 or later
- Python 3.10 or later. If you have no Python, Flow Launcher offers to install it the first time you use a Python plugin.

## Support

Documentation: https://asyntai.com/documentation/integrations/flow-launcher/

Questions: hello@asyntai.com
