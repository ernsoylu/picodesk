/* Minimal single-header test harness for the native RTE primitive tests. */

#ifndef PICODESK_MINI_TEST_H
#define PICODESK_MINI_TEST_H

#include <stdio.h>
#include <stdlib.h>

static int mt_failures = 0;
static int mt_checks = 0;

#define MT_CHECK(cond)                                                      \
    do {                                                                    \
        mt_checks++;                                                        \
        if (!(cond)) {                                                      \
            mt_failures++;                                                  \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                   \
    } while (0)

#define MT_CHECK_EQ(a, b)                                                       \
    do {                                                                        \
        mt_checks++;                                                            \
        long long mt_a = (long long) (a);                                       \
        long long mt_b = (long long) (b);                                       \
        if (mt_a != mt_b) {                                                     \
            mt_failures++;                                                      \
            fprintf(stderr, "FAIL %s:%d: %s == %s (%lld != %lld)\n", __FILE__,  \
                    __LINE__, #a, #b, mt_a, mt_b);                              \
        }                                                                       \
    } while (0)

static inline int mt_summary(const char *suite) {
    if (mt_failures == 0) {
        printf("%s: %d checks passed\n", suite, mt_checks);
        return 0;
    }
    fprintf(stderr, "%s: %d/%d checks FAILED\n", suite, mt_failures, mt_checks);
    return 1;
}

#endif /* PICODESK_MINI_TEST_H */
