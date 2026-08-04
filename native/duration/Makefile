# Builds libduration.so for the ctypes demo (see NATIVE_EXTENSIONS.md).
# Dev/editable-install only -- not built for distribution to other platforms.

CC ?= gcc
CFLAGS = -O2 -Wall -Wextra -fPIC -shared

libduration.so: duration.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f libduration.so

.PHONY: clean
