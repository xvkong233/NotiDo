#!/bin/sh
set -eu
umask 077
mkdir -p /AstrBot/data/plugins /AstrBot/data/plugin_data/astrbot_plugin_notido
python /opt/notido-plugin/deploy/install_plugin.py
exec python main.py
