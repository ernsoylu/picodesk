/* XCP calibration pages (RTE-003).
 *
 * Core 1 (XCP task) writes only the offline RAM page. Core 0 flips the active
 * page pointer exclusively at the model_step() boundary when a page-switch has
 * been requested, giving multi-parameter transactional consistency. Pages live
 * in SRAM2 (BLD-002). NvM persistence is out of scope for v1.0 — pages revert
 * to compiled defaults on power cycle.
 */

#ifndef PICODESK_RTE_CALPAGE_H
#define PICODESK_RTE_CALPAGE_H

#include <stdint.h>

void rte_calpage_request_switch(uint8_t page);

/* Called by the Core 0 dispatcher at the step boundary only. */
void rte_calpage_commit_switch(void);

const void *rte_calpage_active(void);
void *rte_calpage_offline(void);

#endif /* PICODESK_RTE_CALPAGE_H */
