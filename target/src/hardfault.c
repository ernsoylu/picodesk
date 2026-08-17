/* HardFault capture + boot-time post-mortem report (BLD-006).
 *
 * The handler is naked so the exception-stacked frame is untouched: it picks
 * MSP or PSP per EXC_RETURN bit 2 and tails into the C capture routine.
 * Cortex-M0+ instruction set only (no IT blocks, no conditional execution).
 *
 * The record itself lives in .noinit_fault and survives the watchdog reset
 * that follows (BLD-007), so the next boot prints where the previous run
 * died — the only post-mortem channel available, since on-target debugging
 * is out of scope for v1.0 (SRS 1.3).
 */

#include "fault_record.h"

#include <stdio.h>

#include "hardware/structs/sio.h"
#include "hardware/watchdog.h"
#include "pico/platform.h"

#include "rte_sections.h"

volatile fault_record_t g_fault_record RTE_NOINIT_FAULT;

/* Heartbeat snapshot: weakly bound so this file links into firmware images
 * that have no RTE (the value is diagnostic only). */
__attribute__((weak)) volatile uint32_t *fault_heartbeat_source(void) {
    return NULL;
}

static void fault_finish(void) {
    /* Reset promptly through the watchdog: the record is already written and
     * the next boot reports it. Never spin here — a wedged core with
     * interrupts off looks identical to a hang. */
    watchdog_reboot(0, 0, 0);
    for (;;) {
        __asm volatile("wfi");
    }
}

void __not_in_flash_func(fault_capture_hardfault)(uint32_t *frame) {
    const uint32_t seq = (g_fault_record.magic == FAULT_MAGIC)
                             ? g_fault_record.seq + 1u
                             : 1u;
    g_fault_record.kind = FAULT_KIND_HARDFAULT;
    g_fault_record.r0 = frame[0];
    g_fault_record.r1 = frame[1];
    g_fault_record.r2 = frame[2];
    g_fault_record.r3 = frame[3];
    g_fault_record.r12 = frame[4];
    g_fault_record.lr = frame[5];
    g_fault_record.pc = frame[6];
    g_fault_record.psr = frame[7];
    g_fault_record.core = sio_hw->cpuid;
    const volatile uint32_t *hb = fault_heartbeat_source();
    g_fault_record.heartbeat = (hb != NULL) ? *hb : 0u;
    g_fault_record.seq = seq;
    __compiler_memory_barrier();
    g_fault_record.magic = FAULT_MAGIC; /* validity published last */
    fault_finish();
}

__attribute__((naked)) void isr_hardfault(void) {
    __asm volatile(
        "movs r0, #4          \n"
        "mov  r1, lr          \n"
        "tst  r0, r1          \n"
        "beq  1f              \n"
        "mrs  r0, psp         \n"
        "b    2f              \n"
        "1:                   \n"
        "mrs  r0, msp         \n"
        "2:                   \n"
        "ldr  r1, =fault_capture_hardfault \n"
        "bx   r1              \n"
        ".ltorg               \n");
}

void fault_capture_sw(fault_kind_t kind, uint32_t a, uint32_t b) {
    const uint32_t seq = (g_fault_record.magic == FAULT_MAGIC)
                             ? g_fault_record.seq + 1u
                             : 1u;
    g_fault_record.kind = (uint32_t) kind;
    g_fault_record.pc = a; /* assert: file string pointer */
    g_fault_record.lr = b; /* assert: line number         */
    g_fault_record.psr = 0;
    g_fault_record.r0 = g_fault_record.r1 = 0;
    g_fault_record.r2 = g_fault_record.r3 = g_fault_record.r12 = 0;
    g_fault_record.core = sio_hw->cpuid;
    const volatile uint32_t *hb = fault_heartbeat_source();
    g_fault_record.heartbeat = (hb != NULL) ? *hb : 0u;
    g_fault_record.seq = seq;
    __compiler_memory_barrier();
    g_fault_record.magic = FAULT_MAGIC;
    fault_finish();
}

bool fault_record_valid(void) {
    return g_fault_record.magic == FAULT_MAGIC;
}

void fault_report_boot(void) {
    if (!fault_record_valid()) {
        printf("FAULT none boot_wdt=%d\n", (int) watchdog_caused_reboot());
        return;
    }
    /* Machine-parseable: consumed by the Renode fault-injection tests and
     * the GUI diagnostic console (GUI-005). */
    printf("FAULT kind=%lu pc=0x%08lx lr=0x%08lx psr=0x%08lx core=%lu "
           "hb=%lu seq=%lu boot_wdt=%d\n",
           (unsigned long) g_fault_record.kind,
           (unsigned long) g_fault_record.pc,
           (unsigned long) g_fault_record.lr,
           (unsigned long) g_fault_record.psr,
           (unsigned long) g_fault_record.core,
           (unsigned long) g_fault_record.heartbeat,
           (unsigned long) g_fault_record.seq,
           (int) watchdog_caused_reboot());
    g_fault_record.magic = 0; /* consume: next clean boot reports none */
}
