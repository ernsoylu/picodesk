/* XCP protocol core tests (P3, CAL-001 interim slave).
 *
 * Drives the slave exactly as a pyxcp master would: CONNECT, CAL-window
 * DOWNLOAD/UPLOAD with page redirection + transactional switch (RTE-003),
 * and a full dynamic-DAQ bring-up fed from coherent frames (RTE-005).
 */

#include <stdint.h>
#include <string.h>

#include "mini_test.h"
#include "rte_calpage.h"
#include "xcp_core.h"

typedef struct {
    int32_t kp;
    int32_t ki;
    int32_t limit;
} params_t;

static rte_calpage_t cp;
static params_t page_a, page_b;

static uint8_t *hook_offline(void *u) {
    (void) u;
    return (uint8_t *) rte_calpage_offline(&cp);
}

static void hook_switch(void *u) {
    (void) u;
    rte_calpage_request_switch(&cp);
}

static uint8_t hook_active(void *u) {
    (void) u;
    return (uint8_t) (cp.active_idx & 1u);
}

#define MAX_DTOS 64
static uint8_t dto_log[MAX_DTOS][XCP_MAX_DTO];
static uint8_t dto_len[MAX_DTOS];
static int dto_count;

static void hook_dto(void *u, const uint8_t *pkt, uint8_t len) {
    (void) u;
    if (dto_count < MAX_DTOS) {
        memcpy(dto_log[dto_count], pkt, len);
        dto_len[dto_count] = len;
        dto_count++;
    }
}

static xcp_slave_t x;
static uint8_t resp[XCP_MAX_CTO];

static uint8_t run(const uint8_t *cmd, uint8_t len) {
    memset(resp, 0, sizeof resp);
    return xcp_command(&x, cmd, len, resp);
}

static void wr32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t) v;
    p[1] = (uint8_t) (v >> 8);
    p[2] = (uint8_t) (v >> 16);
    p[3] = (uint8_t) (v >> 24);
}

int main(void) {
    const params_t defaults = {9830, 655, 32767};
    rte_calpage_init(&cp, &page_a, &page_b, sizeof(params_t), &defaults);

    x.cal_logical_base = (uint8_t *) &page_a; /* fixed logical window */
    x.cal_size = sizeof(params_t);
    x.cal_offline = hook_offline;
    x.cal_request_switch = hook_switch;
    x.cal_active_page = hook_active;
    x.mem_ok = NULL; /* permissive natively */
    x.dto_send = hook_dto;
    x.user = NULL;
    xcp_init(&x);

    /* Commands before CONNECT are rejected. */
    MT_CHECK_EQ(run((const uint8_t[]) {0xFD}, 1), 2);
    MT_CHECK_EQ(resp[0], 0xFE);

    /* CONNECT. */
    MT_CHECK_EQ(run((const uint8_t[]) {0xFF, 0x00}, 2), 8);
    MT_CHECK_EQ(resp[0], 0xFF);
    MT_CHECK_EQ(resp[1], 0x05); /* CAL/PAG | DAQ */
    MT_CHECK_EQ(resp[3], XCP_MAX_CTO);

    /* CAL: SET_MTA into the logical window -> DOWNLOAD hits the OFFLINE
     * page; the active page stays untouched until the commit (RTE-003). */
    uint8_t set_mta[8] = {0xF6, 0, 0, 0};
    wr32(&set_mta[4], (uint32_t) (uintptr_t) &page_a); /* kp offset 0 */
    MT_CHECK_EQ(run(set_mta, 8), 1);
    uint8_t dl[6] = {0xF0, 4, 0, 0, 0, 0};
    wr32(&dl[2], 11469); /* new kp */
    MT_CHECK_EQ(run(dl, 6), 1);
    MT_CHECK_EQ(((params_t *) rte_calpage_offline(&cp))->kp, 11469);
    MT_CHECK_EQ(((const params_t *) rte_calpage_active(&cp))->kp, 9830);

    /* Verify-after-write reads the offline page too (SHORT_UPLOAD). */
    uint8_t su[8] = {0xF4, 4, 0, 0};
    wr32(&su[4], (uint32_t) (uintptr_t) &page_a);
    MT_CHECK_EQ(run(su, 8), 5);
    MT_CHECK_EQ((int32_t) (resp[1] | (resp[2] << 8) | (resp[3] << 16) | (resp[4] << 24)),
                11469);

    /* SET_CAL_PAGE to the inactive page arms the switch; the "fast loop"
     * commits at its step boundary. */
    MT_CHECK_EQ(run((const uint8_t[]) {0xEB, 0x03, 0, 1}, 4), 1);
    MT_CHECK(cp.switch_request == 1);
    MT_CHECK(rte_calpage_commit(&cp)); /* ISR step boundary */
    MT_CHECK_EQ(((const params_t *) rte_calpage_active(&cp))->kp, 11469);
    MT_CHECK_EQ(run((const uint8_t[]) {0xEA, 0x03, 0}, 3), 4);
    MT_CHECK_EQ(resp[3], 1); /* GET_CAL_PAGE reports the new page */

    /* DAQ bring-up: 1 list, 1 ODT, 2 entries into a 16-byte frame. */
    typedef struct {
        uint32_t tick;
        int32_t torque;
        int32_t iq;
        int32_t speed;
    } frame_t;
    const uint32_t base = 0x40000u; /* arbitrary logical frame address */

    MT_CHECK_EQ(run((const uint8_t[]) {0xD6}, 1), 1);                      /* FREE_DAQ  */
    MT_CHECK_EQ(run((const uint8_t[]) {0xD5, 0, 1, 0}, 4), 1);             /* ALLOC_DAQ */
    MT_CHECK_EQ(run((const uint8_t[]) {0xD4, 0, 0, 0, 1}, 5), 1);          /* ALLOC_ODT */
    MT_CHECK_EQ(run((const uint8_t[]) {0xD3, 0, 0, 0, 0, 2}, 6), 1);       /* ALLOC_ENT */
    MT_CHECK_EQ(run((const uint8_t[]) {0xE2, 0, 0, 0, 0, 0}, 6), 1);       /* SET_PTR   */
    uint8_t wd[8] = {0xE1, 0, 4, 0};
    wr32(&wd[4], base + 4); /* torque */
    MT_CHECK_EQ(run(wd, 8), 1);
    wr32(&wd[4], base + 12); /* speed */
    MT_CHECK_EQ(run(wd, 8), 1);
    MT_CHECK_EQ(run((const uint8_t[]) {0xE0, 0, 0, 0, 0, 0, 1, 0}, 8), 1); /* MODE ev0  */
    MT_CHECK_EQ(run((const uint8_t[]) {0xDE, 1, 0, 0}, 4), 2);             /* START     */

    frame_t f = {7, -1234, 55, 4321};
    xcp_daq_event0(&x, (const uint8_t *) &f, sizeof f, base);
    MT_CHECK_EQ(dto_count, 1);
    MT_CHECK_EQ(dto_len[0], 9); /* PID + 4 + 4 */
    MT_CHECK_EQ(dto_log[0][0], 0); /* absolute PID 0 */
    int32_t got_torque, got_speed;
    memcpy(&got_torque, &dto_log[0][1], 4);
    memcpy(&got_speed, &dto_log[0][5], 4);
    MT_CHECK_EQ(got_torque, -1234);
    MT_CHECK_EQ(got_speed, 4321);

    /* Entries outside the coherent frame are suppressed, not sampled live. */
    dto_count = 0;
    MT_CHECK_EQ(run((const uint8_t[]) {0xE2, 0, 0, 0, 0, 1}, 6), 1);
    wr32(&wd[4], base + 64); /* out of frame */
    MT_CHECK_EQ(run(wd, 8), 1);
    xcp_daq_event0(&x, (const uint8_t *) &f, sizeof f, base);
    MT_CHECK_EQ(dto_count, 0);

    /* STOP + DISCONNECT. */
    MT_CHECK_EQ(run((const uint8_t[]) {0xDD, 0}, 2), 1);
    MT_CHECK_EQ(run((const uint8_t[]) {0xFE}, 1), 1);
    MT_CHECK(!x.connected);

    return mt_summary("xcp_core");
}
