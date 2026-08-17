#include "xcp_core.h"

#include <string.h>

/* Command PIDs (ASAM XCP part 2). */
#define CMD_CONNECT 0xFF
#define CMD_DISCONNECT 0xFE
#define CMD_GET_STATUS 0xFD
#define CMD_SYNCH 0xFC
#define CMD_SET_MTA 0xF6
#define CMD_UPLOAD 0xF5
#define CMD_SHORT_UPLOAD 0xF4
#define CMD_DOWNLOAD 0xF0
#define CMD_SET_CAL_PAGE 0xEB
#define CMD_GET_CAL_PAGE 0xEA
#define CMD_SET_DAQ_PTR 0xE2
#define CMD_WRITE_DAQ 0xE1
#define CMD_SET_DAQ_LIST_MODE 0xE0
#define CMD_START_STOP_DAQ_LIST 0xDE
#define CMD_START_STOP_SYNCH 0xDD
#define CMD_GET_DAQ_PROCESSOR_INFO 0xDA
#define CMD_GET_DAQ_RESOLUTION_INFO 0xD9
#define CMD_FREE_DAQ 0xD6
#define CMD_ALLOC_DAQ 0xD5
#define CMD_ALLOC_ODT 0xD4
#define CMD_ALLOC_ODT_ENTRY 0xD3

#define PID_RES 0xFF
#define PID_ERR 0xFE

#define ERR_CMD_SYNTAX 0x21
#define ERR_CMD_UNKNOWN 0x20
#define ERR_OUT_OF_RANGE 0x22
#define ERR_ACCESS_DENIED 0x24
#define ERR_MEMORY_OVERFLOW 0x30
#define ERR_DAQ_ACTIVE 0x26

static uint16_t rd16(const uint8_t *p) {
    return (uint16_t) (p[0] | (p[1] << 8));
}

static uint32_t rd32(const uint8_t *p) {
    return (uint32_t) (p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t) p[3] << 24));
}

void xcp_init(xcp_slave_t *x) {
    x->connected = false;
    x->mta = 0;
    x->n_daq_allocated = 0;
    memset(x->daq, 0, sizeof x->daq);
    x->ptr_daq = 0;
    x->ptr_odt = 0;
    x->ptr_entry = 0;
    x->daq_running = false;
}

/* Redirect a CAL-window address to the offline page; other addresses map
 * to themselves after passing the guard. Returns NULL when denied. */
static uint8_t *resolve(xcp_slave_t *x, uint32_t addr, uint32_t len, bool write) {
    const uint32_t cal_base = (uint32_t) (uintptr_t) x->cal_logical_base;
    if (addr >= cal_base && addr + len <= cal_base + x->cal_size) {
        return x->cal_offline(x->user) + (addr - cal_base);
    }
    if (x->mem_ok != NULL && !x->mem_ok(x->user, addr, len, write)) {
        return NULL;
    }
    return (uint8_t *) (uintptr_t) addr;
}

static uint8_t err(uint8_t *resp, uint8_t code) {
    resp[0] = PID_ERR;
    resp[1] = code;
    return 2;
}

static void daq_stop_all(xcp_slave_t *x) {
    for (unsigned i = 0; i < XCP_MAX_DAQ_LISTS; i++) {
        x->daq[i].running = false;
        x->daq[i].selected = false;
    }
    x->daq_running = false;
}

uint8_t xcp_command(xcp_slave_t *x, const uint8_t *cmd, uint8_t cmd_len,
                    uint8_t *resp) {
    if (cmd_len == 0) {
        return err(resp, ERR_CMD_SYNTAX);
    }
    const uint8_t pid = cmd[0];

    if (pid == CMD_CONNECT) {
        x->connected = true;
        daq_stop_all(x);
        resp[0] = PID_RES;
        resp[1] = 0x05; /* RESOURCE: CAL/PAG | DAQ */
        resp[2] = 0x00; /* COMM_MODE_BASIC: little-endian, byte granularity */
        resp[3] = XCP_MAX_CTO;
        resp[4] = (uint8_t) (XCP_MAX_DTO & 0xFF);
        resp[5] = (uint8_t) (XCP_MAX_DTO >> 8);
        resp[6] = 0x01; /* protocol layer version */
        resp[7] = 0x01; /* transport layer version */
        return 8;
    }
    if (!x->connected) {
        return err(resp, ERR_CMD_UNKNOWN);
    }

    switch (pid) {
        case CMD_DISCONNECT:
            daq_stop_all(x);
            x->connected = false;
            resp[0] = PID_RES;
            return 1;

        case CMD_GET_STATUS:
            resp[0] = PID_RES;
            resp[1] = (uint8_t) (x->daq_running ? 0x40 : 0x00); /* DAQ_RUNNING */
            resp[2] = 0x00; /* no protection (XCP security out of scope) */
            resp[3] = 0x00;
            resp[4] = 0x00;
            resp[5] = 0x00;
            return 6;

        case CMD_SYNCH:
            return err(resp, 0x00); /* ERR_CMD_SYNCH positive-by-convention */

        case CMD_SET_MTA:
            if (cmd_len < 8) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            x->mta = rd32(&cmd[4]);
            resp[0] = PID_RES;
            return 1;

        case CMD_UPLOAD: {
            if (cmd_len < 2) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            const uint8_t n = cmd[1];
            if (n > XCP_MAX_CTO - 1u) {
                return err(resp, ERR_OUT_OF_RANGE);
            }
            const uint8_t *src = resolve(x, x->mta, n, false);
            if (src == NULL) {
                return err(resp, ERR_ACCESS_DENIED);
            }
            resp[0] = PID_RES;
            memcpy(&resp[1], src, n);
            x->mta += n;
            return (uint8_t) (1 + n);
        }

        case CMD_SHORT_UPLOAD: {
            if (cmd_len < 8) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            const uint8_t n = cmd[1];
            if (n > XCP_MAX_CTO - 1u) {
                return err(resp, ERR_OUT_OF_RANGE);
            }
            const uint8_t *src = resolve(x, rd32(&cmd[4]), n, false);
            if (src == NULL) {
                return err(resp, ERR_ACCESS_DENIED);
            }
            resp[0] = PID_RES;
            memcpy(&resp[1], src, n);
            return (uint8_t) (1 + n);
        }

        case CMD_DOWNLOAD: {
            if (cmd_len < 2) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            const uint8_t n = cmd[1];
            if (n == 0 || (uint8_t) (n + 2u) > cmd_len) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            uint8_t *dst = resolve(x, x->mta, n, true);
            if (dst == NULL) {
                return err(resp, ERR_ACCESS_DENIED);
            }
            memcpy(dst, &cmd[2], n);
            x->mta += n;
            resp[0] = PID_RES;
            return 1;
        }

        case CMD_SET_CAL_PAGE:
            /* mode cmd[1] (ECU/XCP bits ignored: one logical segment), page
             * cmd[3]. Selecting the page that is not active arms the
             * transactional switch; the fast loop commits it (RTE-003). */
            if (cmd_len < 4) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            if (cmd[3] > 1) {
                return err(resp, ERR_OUT_OF_RANGE);
            }
            if (cmd[3] != x->cal_active_page(x->user)) {
                x->cal_request_switch(x->user);
            }
            resp[0] = PID_RES;
            return 1;

        case CMD_GET_CAL_PAGE:
            resp[0] = PID_RES;
            resp[1] = 0;
            resp[2] = 0;
            resp[3] = x->cal_active_page(x->user);
            return 4;

        case CMD_GET_DAQ_PROCESSOR_INFO:
            resp[0] = PID_RES;
            resp[1] = 0x01; /* DAQ_PROPERTIES: dynamic configuration */
            resp[2] = XCP_MAX_DAQ_LISTS;
            resp[3] = 0x00;
            resp[4] = 0x01; /* MAX_EVENT_CHANNEL = 1 */
            resp[5] = 0x00;
            resp[6] = 0x00; /* MIN_DAQ */
            resp[7] = 0x00; /* DAQ_KEY_BYTE: absolute ODT numbers */
            return 8;

        case CMD_GET_DAQ_RESOLUTION_INFO:
            resp[0] = PID_RES;
            resp[1] = 1; /* GRANULARITY_ODT_ENTRY_SIZE_DAQ */
            resp[2] = XCP_MAX_DTO - 1u; /* MAX_ODT_ENTRY_SIZE_DAQ */
            resp[3] = 1; /* GRANULARITY_STIM */
            resp[4] = 0; /* MAX_ODT_ENTRY_SIZE_STIM */
            resp[5] = 0; /* TIMESTAMP_MODE: none */
            resp[6] = 0;
            resp[7] = 0;
            return 8;

        case CMD_FREE_DAQ:
            daq_stop_all(x);
            x->n_daq_allocated = 0;
            memset(x->daq, 0, sizeof x->daq);
            resp[0] = PID_RES;
            return 1;

        case CMD_ALLOC_DAQ: {
            if (cmd_len < 4) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            const uint16_t count = rd16(&cmd[2]);
            if (count > XCP_MAX_DAQ_LISTS) {
                return err(resp, ERR_MEMORY_OVERFLOW);
            }
            x->n_daq_allocated = count;
            resp[0] = PID_RES;
            return 1;
        }

        case CMD_ALLOC_ODT: {
            if (cmd_len < 5) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            const uint16_t daq = rd16(&cmd[2]);
            if (daq >= x->n_daq_allocated || cmd[4] > XCP_MAX_ODT_PER_LIST) {
                return err(resp, ERR_MEMORY_OVERFLOW);
            }
            x->daq[daq].n_odts = cmd[4];
            resp[0] = PID_RES;
            return 1;
        }

        case CMD_ALLOC_ODT_ENTRY: {
            if (cmd_len < 6) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            const uint16_t daq = rd16(&cmd[2]);
            const uint8_t odt = cmd[4];
            if (daq >= x->n_daq_allocated || odt >= x->daq[daq].n_odts
                || cmd[5] > XCP_MAX_ENTRIES_PER_ODT) {
                return err(resp, ERR_MEMORY_OVERFLOW);
            }
            x->daq[daq].odts[odt].n_entries = cmd[5];
            resp[0] = PID_RES;
            return 1;
        }

        case CMD_SET_DAQ_PTR: {
            if (cmd_len < 6) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            const uint16_t daq = rd16(&cmd[2]);
            if (daq >= x->n_daq_allocated || cmd[4] >= x->daq[daq].n_odts
                || cmd[5] >= x->daq[daq].odts[cmd[4]].n_entries) {
                return err(resp, ERR_OUT_OF_RANGE);
            }
            x->ptr_daq = daq;
            x->ptr_odt = cmd[4];
            x->ptr_entry = cmd[5];
            resp[0] = PID_RES;
            return 1;
        }

        case CMD_WRITE_DAQ: {
            if (cmd_len < 8) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            xcp_odt_entry_t *e =
                &x->daq[x->ptr_daq].odts[x->ptr_odt].entries[x->ptr_entry];
            e->len = cmd[2];
            e->addr = rd32(&cmd[4]);
            if (x->ptr_entry + 1u < x->daq[x->ptr_daq].odts[x->ptr_odt].n_entries) {
                x->ptr_entry++;
            }
            resp[0] = PID_RES;
            return 1;
        }

        case CMD_SET_DAQ_LIST_MODE: {
            if (cmd_len < 8) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            const uint16_t daq = rd16(&cmd[2]);
            if (daq >= x->n_daq_allocated) {
                return err(resp, ERR_OUT_OF_RANGE);
            }
            x->daq[daq].event = rd16(&cmd[4]);
            resp[0] = PID_RES;
            return 1;
        }

        case CMD_START_STOP_DAQ_LIST: {
            if (cmd_len < 4) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            const uint8_t mode = cmd[1];
            const uint16_t daq = rd16(&cmd[2]);
            if (daq >= x->n_daq_allocated) {
                return err(resp, ERR_OUT_OF_RANGE);
            }
            if (mode == 0) {
                x->daq[daq].running = false;
            } else if (mode == 1) {
                x->daq[daq].running = true;
                x->daq_running = true;
            } else {
                x->daq[daq].selected = true;
            }
            resp[0] = PID_RES;
            resp[1] = (uint8_t) (daq * XCP_MAX_ODT_PER_LIST); /* FIRST_PID */
            return 2;
        }

        case CMD_START_STOP_SYNCH: {
            if (cmd_len < 2) {
                return err(resp, ERR_CMD_SYNTAX);
            }
            const uint8_t mode = cmd[1];
            if (mode == 0) {
                daq_stop_all(x);
            } else if (mode == 1) {
                for (unsigned i = 0; i < XCP_MAX_DAQ_LISTS; i++) {
                    if (x->daq[i].selected) {
                        x->daq[i].running = true;
                        x->daq[i].selected = false;
                        x->daq_running = true;
                    }
                }
            } else if (mode == 2) {
                for (unsigned i = 0; i < XCP_MAX_DAQ_LISTS; i++) {
                    if (x->daq[i].selected) {
                        x->daq[i].running = false;
                        x->daq[i].selected = false;
                    }
                }
            }
            resp[0] = PID_RES;
            return 1;
        }

        default:
            return err(resp, ERR_CMD_UNKNOWN);
    }
}

void xcp_daq_event0(xcp_slave_t *x, const uint8_t *frame, size_t frame_len,
                    uint32_t frame_base_addr) {
    if (!x->daq_running) {
        return;
    }
    uint8_t pkt[XCP_MAX_DTO];
    for (unsigned list = 0; list < x->n_daq_allocated; list++) {
        xcp_daq_list_t *dl = &x->daq[list];
        if (!dl->running || dl->event != 0) {
            continue;
        }
        for (unsigned odt = 0; odt < dl->n_odts; odt++) {
            const xcp_odt_t *o = &dl->odts[odt];
            uint8_t len = 1;
            pkt[0] = (uint8_t) (list * XCP_MAX_ODT_PER_LIST + odt); /* abs PID */
            bool valid = o->n_entries > 0;
            for (unsigned e = 0; e < o->n_entries; e++) {
                const xcp_odt_entry_t *entry = &o->entries[e];
                const uint32_t off = entry->addr - frame_base_addr;
                if (off >= frame_len || off + entry->len > frame_len
                    || len + entry->len > XCP_MAX_DTO) {
                    valid = false; /* entry outside the coherent frame */
                    break;
                }
                memcpy(&pkt[len], frame + off, entry->len);
                len = (uint8_t) (len + entry->len);
            }
            if (valid) {
                x->dto_send(x->user, pkt, len);
            }
        }
    }
}
