#include "fault_inject.h"

#include <stdio.h>

#include "FreeRTOS.h"
#include "task.h"
#include "pico/stdlib.h"

#include "fault_record.h"
#include "rte_sections.h"
#include "rte_watchdog.h"

static volatile uint32_t s_isr_wedged RTE_SHARED;

bool fault_inject_isr_wedged(void) {
    return s_isr_wedged != 0u;
}

void fault_inject_poll(void) {
    const int c = getchar_timeout_us(0);
    switch (c) {
        case 'h':
            /* Branch to an unmapped, non-executable address: an
             * unconditional HardFault (prefetch abort) on ARMv6-M.
             *
             * Two more obvious injections were tried first and both failed
             * to fault at all: an unaligned load from a low address is
             * split by GCC into byte loads (and low addresses are mapped
             * bootrom anyway), and an undefined instruction is not trapped
             * by every model. A fetch from unmapped space is the one that
             * genuinely aborts on hardware and in emulation. */
            printf("INJECT hardfault\n");
            ((void (*)(void)) 0xF0000001u)();
            break;
        case 'a':
            printf("INJECT assert\n");
            configASSERT(0);
            break;
        case 'w':
            printf("INJECT isr_wedge\n");
            s_isr_wedged = 1u;
            break;
        case 'm':
            printf("INJECT monitor_stall\n");
            rte_watchdog_inject_stall();
            break;
        default:
            break;
    }
}
