# for testing and documentation only. this is deployed to bare metal
FROM ubuntu:26.04

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 \
      python3-serial \
      python3-evdev \
      tzdata \
      udev

COPY listen.py   /usr/local/bin/listen-slaughter-measurements
COPY shout.py    /usr/local/bin/shout-slaughter-measurements
COPY simulate.py /usr/local/bin/simulate-slaughter-measurements
# listen and shout import these from their own directory.
COPY local_time.py /usr/local/bin/local_time.py
COPY matching.py   /usr/local/bin/matching.py
COPY scan_sequence.py /usr/local/bin/scan_sequence.py

RUN chmod +x /usr/local/bin/listen-slaughter-measurements \
             /usr/local/bin/shout-slaughter-measurements \
             /usr/local/bin/simulate-slaughter-measurements

ENTRYPOINT ["shout-slaughter-measurements"]
