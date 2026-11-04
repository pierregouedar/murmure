#!/bin/sh
# Notification command for timers and reminders: MURMURE_NOTIFY_CMD=/path/to/notify-ntfy.sh
# Called as: notify-ntfy.sh "<title>" "<message>". Set NTFY_URL to your topic (https://ntfy.sh/my-secret-topic or a self-hosted one).
curl -fsS -H "Title: $1" -H "Priority: high" -H "Tags: alarm_clock" -d "$2" "${NTFY_URL:?set NTFY_URL}"
