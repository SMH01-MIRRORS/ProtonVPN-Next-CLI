import os
import asyncio
import glob
import subprocess
from telethon import TelegramClient
from telethon.sessions import StringSession

# Environment variables from Woodpecker secrets
API_ID = int(os.environ.get('TG_API_ID', 0))
API_HASH = os.environ.get('TG_API_HASH')
# Use Bot Token instead of User Session
BOT_TOKEN = os.environ.get('TG_BOT_TOKEN')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '0')

# Try to parse CHAT_ID as int, otherwise keep as string (for usernames)
try:
    CHAT_ID = int(TG_CHAT_ID)
except ValueError:
    CHAT_ID = TG_CHAT_ID

# Woodpecker default environment variables
COMMIT_AUTHOR = os.environ.get('CI_COMMIT_AUTHOR', 'Unknown')
COMMIT_SHA = os.environ.get('CI_COMMIT_SHA', 'none')[:8]
REPO_NAME = os.environ.get('CI_REPO', 'ProtonVPN-Next')
BRANCH = os.environ.get('CI_COMMIT_BRANCH', 'unknown')
EVENT = os.environ.get('CI_PIPELINE_EVENT', 'manual')
TAG = os.environ.get('CI_COMMIT_TAG')

def get_commit_summary():
    """
    Returns a summary of commits. If it's a push event, it looks at the last 10 commits
    and groups all consecutive commits by the same author to provide a batch summary.
    """
    event = os.environ.get('CI_PIPELINE_EVENT')
    author = os.environ.get('CI_COMMIT_AUTHOR')
    full_message = os.environ.get('CI_COMMIT_MESSAGE', 'No message')
    # Default to the first line of the current commit message
    default_msg = full_message.split('\n')[0].strip()

    if event == 'push' and author:
        try:
            # Get last 10 commits with author and subject
            # Format: AuthorName|Subject
            log_output = subprocess.check_output(
                ['git', 'log', '-10', '--pretty=format:%an|%s'],
                stderr=subprocess.DEVNULL,
                text=True
            ).strip()

            if log_output:
                lines = log_output.split('\n')
                relevant_messages = []
                for line in lines:
                    if '|' in line:
                        commit_author, subject = line.split('|', 1)
                        # Woodpecker author might be 'Name <email>', git %an is 'Name'
                        # Or they might match exactly. We check if author contains commit_author or vice-versa.
                        if commit_author in author or author in commit_author:
                            relevant_messages.append(subject)
                        else:
                            # Stop if we hit a commit by a different author
                            break

                if len(relevant_messages) > 1:
                    # Reverse to chronological order: oldest to newest
                    relevant_messages.reverse()
                    return "\n" + "\n".join([f"  • {m}" for m in relevant_messages])
                elif len(relevant_messages) == 1:
                    return relevant_messages[0]
        except Exception:
            # Fallback to default if git command fails
            pass

    return default_msg

async def main():
    if not all([API_ID, API_HASH, BOT_TOKEN, CHAT_ID]):
        print("Error: Missing Telegram configuration secrets (API_ID, API_HASH, BOT_TOKEN, TG_CHAT_ID)!")
        return

    # Find CLI binaries
    binary_files = []
    if os.path.exists('dist/pvpn-next-linux'):
        binary_files.append('dist/pvpn-next-linux')
    if os.path.exists('dist/pvpn-next-windows.exe'):
        binary_files.append('dist/pvpn-next-windows.exe')

    if not binary_files:
        print("Error: No binary files found!")
        return

    print(f"Connecting to Telegram via Bot and uploading {len(binary_files)} file(s)...")
    client = TelegramClient(StringSession(''), API_ID, API_HASH)

    # Authenticate using the Bot Token
    await client.start(bot_token=BOT_TOKEN)

    commit_summary = get_commit_summary()
    commit_label = "Commits" if "\n" in commit_summary else "Commit"

    for bin_path in binary_files:
        file_name = os.path.basename(bin_path)
        platform_str = "Windows" if file_name.endswith(".exe") else "Linux"

        tag_str = f"\n🏷️ **Tag:** `{TAG}`" if TAG else ""

        caption = (
            f"🚀 **New Build ({platform_str}): {REPO_NAME}**\n\n"
            f"📝 **{commit_label}:** {commit_summary}\n"
            f"👤 **Author:** {COMMIT_AUTHOR}\n"
            f"🌿 **Branch:** `{BRANCH}`{tag_str}\n"
            f"🔢 **Hash:** `{COMMIT_SHA}`\n"
            f"⚡ **Event:** `{EVENT.upper()}`"
        )

        try:
            print(f"Uploading {file_name} to {CHAT_ID}...")
            # We try to send directly using the CHAT_ID (int or str)
            await client.send_file(
                CHAT_ID,
                bin_path,
                caption=caption,
                parse_mode='md'
            )
            print(f"Upload successful: {file_name}")
        except Exception as e:
            print(f"Error during upload of {file_name}: {e}")
            if isinstance(CHAT_ID, int) and CHAT_ID > 0:
                print(f"💡 IMPORTANT: Your Chat ID {CHAT_ID} looks like a Channel ID.")
                print(f"Try changing TG_CHAT_ID to -100{CHAT_ID} in your Woodpecker CI secrets.")
            print("\n💡 Troubleshooting:")
            print("1. If CHAT_ID is a user, they MUST send /start to the bot first.")
            print("2. If CHAT_ID is a channel/group, ensure it starts with '-' or '-100'.")
            print("3. Ensure the Bot is an administrator in the channel/group with 'Post Messages' permission.")

    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())