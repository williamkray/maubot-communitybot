#!/usr/bin/env bash

docker run \
  -v "$PWD"/data:/data \
  -v "$PWD":/opt/communitybot \
  --user $UID:$GID \
  --name communitybot \
  dock.mau.dev/maubot/maubot:standalone \
  python3 -m maubot.standalone \
    -m /opt/communitybot/maubot.yaml \
    -c /data/config.yaml \
    -b /opt/communitybot/example-standalone-config.yaml

