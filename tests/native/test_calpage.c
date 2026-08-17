/* CAL page (RTE-003) unit + handshake stress tests.
 *
 * The stress test emulates the real topology: an "XCP" thread edits the
 * offline page with internally-consistent parameter sets and requests
 * switches; a "fast loop" thread commits at step boundaries and checks it
 * only ever observes consistent sets — multi-parameter transactionality.
 */

#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <string.h>

#include "mini_test.h"
#include "rte_calpage.h"

typedef struct {
    int32_t a;
    int32_t b;
    int32_t checksum; /* always a + b */
} params_t;

static rte_calpage_t cp;
static params_t page_a, page_b;

static atomic_bool stop;
static atomic_ulong inconsistent;
static atomic_ulong commits;

static void *fast_loop(void *arg) {
    (void) arg;
    while (!atomic_load(&stop)) {
        if (rte_calpage_commit(&cp)) {
            atomic_fetch_add(&commits, 1);
        }
        const params_t *active = (const params_t *) rte_calpage_active(&cp);
        params_t snap = *active; /* fast step reads the active page */
        if (snap.a + snap.b != snap.checksum) {
            atomic_fetch_add(&inconsistent, 1);
        }
    }
    return NULL;
}

static void *xcp_editor(void *arg) {
    (void) arg;
    int32_t v = 1;
    while (!atomic_load(&stop)) {
        while (!rte_calpage_sync_offline(&cp) && !atomic_load(&stop)) {
            /* switch still pending: offline page is hands-off */
        }
        params_t *offline = (params_t *) rte_calpage_offline(&cp);
        offline->a = v;
        offline->b = v * 7;
        offline->checksum = offline->a + offline->b;
        rte_calpage_request_switch(&cp);
        v++;
    }
    return NULL;
}

int main(void) {
    const params_t defaults = {100, 200, 300};
    rte_calpage_init(&cp, &page_a, &page_b, sizeof(params_t), &defaults);

    /* Unit: both pages boot from defaults; offline edits stay invisible
     * until commit; commit flips exactly once per request. */
    const params_t *active = (const params_t *) rte_calpage_active(&cp);
    MT_CHECK_EQ(active->checksum, 300);
    params_t *off = (params_t *) rte_calpage_offline(&cp);
    MT_CHECK(off != (params_t *) active);
    off->a = 1;
    off->b = 2;
    off->checksum = 3;
    MT_CHECK_EQ(((const params_t *) rte_calpage_active(&cp))->checksum, 300);
    MT_CHECK(!rte_calpage_commit(&cp)); /* nothing requested yet */
    rte_calpage_request_switch(&cp);
    MT_CHECK(!rte_calpage_sync_offline(&cp)); /* pending -> refuse */
    MT_CHECK(rte_calpage_commit(&cp));
    MT_CHECK_EQ(((const params_t *) rte_calpage_active(&cp))->checksum, 3);
    MT_CHECK(!rte_calpage_commit(&cp)); /* request consumed */
    MT_CHECK(rte_calpage_sync_offline(&cp));
    MT_CHECK_EQ(((const params_t *) rte_calpage_offline(&cp))->checksum, 3);
    MT_CHECK_EQ(cp.switch_count, 1);

    /* Handshake stress. */
    pthread_t ft, xt;
    pthread_create(&ft, NULL, fast_loop, NULL);
    pthread_create(&xt, NULL, xcp_editor, NULL);
    struct timespec t = {2, 0};
    nanosleep(&t, NULL);
    atomic_store(&stop, true);
    pthread_join(ft, NULL);
    pthread_join(xt, NULL);

    MT_CHECK_EQ(atomic_load(&inconsistent), 0);
    MT_CHECK(atomic_load(&commits) > 100);
    printf("stress: commits=%lu inconsistent=%lu\n", atomic_load(&commits),
           atomic_load(&inconsistent));

    return mt_summary("calpage");
}
