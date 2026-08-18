/* Implementation of the XCPlite platform shim (see picodesk_platform.h). */

#include "picodesk_platform.h"

#include <stdio.h>

#ifdef PICODESK_NATIVE

/* Host build for tests/native: pthreads and CLOCK_MONOTONIC stand in for the
 * RTOS and the RP2040 timer. Only the protocol behaviour is under test here;
 * the timing guarantees this shim exists to serve are hardware properties and
 * are covered by the Renode suite and the on-target campaign. */
#include <pthread.h>
#include <stdlib.h>
#include <time.h>

void mutexInit(MUTEX *m, bool recursive, uint32_t spinCount) {
    (void) recursive;
    (void) spinCount;
    pthread_mutex_t *mtx = malloc(sizeof *mtx);
    pthread_mutexattr_t attr;
    pthread_mutexattr_init(&attr);
    pthread_mutexattr_settype(&attr, PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_init(mtx, &attr);
    pthread_mutexattr_destroy(&attr);
    *m = mtx;
}

void mutexDestroy(MUTEX *m) {
    if (*m != NULL) {
        pthread_mutex_destroy((pthread_mutex_t *) *m);
        free(*m);
        *m = NULL;
    }
}

void mutexLock(MUTEX *m) { pthread_mutex_lock((pthread_mutex_t *) *m); }

void mutexUnlock(MUTEX *m) { pthread_mutex_unlock((pthread_mutex_t *) *m); }

bool clockInit(void) { return true; }

uint64_t clockGet(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t) ts.tv_sec * 1000000u + (uint64_t) (ts.tv_nsec / 1000);
}

#else /* target build */

#include "FreeRTOS.h"
#include "semphr.h"
#include "hardware/timer.h"

void mutexInit(MUTEX *m, bool recursive, uint32_t spinCount) {
    (void) spinCount;
    /* Recursive throughout: XCPlite takes the DAQ mutex re-entrantly on
     * some paths, and a plain mutex would deadlock the XCP task. */
    (void) recursive;
    *m = (void *) xSemaphoreCreateRecursiveMutex();
    configASSERT(*m != NULL);
}

void mutexDestroy(MUTEX *m) {
    if (*m != NULL) {
        vSemaphoreDelete((SemaphoreHandle_t) *m);
        *m = NULL;
    }
}

void mutexLock(MUTEX *m) {
    xSemaphoreTakeRecursive((SemaphoreHandle_t) *m, portMAX_DELAY);
}

void mutexUnlock(MUTEX *m) { xSemaphoreGiveRecursive((SemaphoreHandle_t) *m); }

bool clockInit(void) {
    return true; /* the RP2040 timer runs from reset */
}

uint64_t clockGet(void) { return time_us_64(); }

#endif /* PICODESK_NATIVE */

char *clockGetString(char *s, uint32_t l, uint64_t c) {
    snprintf(s, l, "%" PRIu64 " us", c);
    return s;
}
