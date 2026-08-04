/* Plain C port of amao.cli._format_duration -- the simplest possible
 * Python<->native example: a pure scalar-in/string-out C function, called
 * from Python via ctypes (no build system, no wrapper library, just a
 * shared object and a hand-written function signature).
 *
 * Rounding note: Python's round() uses round-half-to-even (banker's
 * rounding); this uses round-half-up. They differ only when `seconds`
 * lands on an exact .5 boundary -- vanishingly unlikely for a value that
 * comes from averaging real wall-clock durations, but a genuine, documented
 * behavioral difference, not something hidden.
 */
#include <stdio.h>

void format_duration(double seconds, char *buf, int buf_size) {
    long total = (long)(seconds + 0.5);
    long hours = total / 3600;
    long remainder = total % 3600;
    long minutes = remainder / 60;
    long secs = remainder % 60;

    if (hours > 0) {
        snprintf(buf, buf_size, "%ldh%ldm%lds", hours, minutes, secs);
    } else if (minutes > 0) {
        snprintf(buf, buf_size, "%ldm%lds", minutes, secs);
    } else {
        snprintf(buf, buf_size, "%lds", secs);
    }
}
