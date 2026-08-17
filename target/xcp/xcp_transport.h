/* XCP-on-CDC transport framing (CAL-001).
 *
 * Wire format matches pyxcp's SxI HEADER_LEN_CTR_WORD serial transport:
 *   uint16 LEN (little-endian, packet bytes) | uint16 CTR | packet
 * No checksum (CDC is already CRC-protected at the USB layer).
 */

#ifndef PICODESK_XCP_TRANSPORT_H
#define PICODESK_XCP_TRANSPORT_H

#include <stdint.h>

#define XCP_TL_HEADER_LEN 4u
#define XCP_TL_MAX_PACKET 64u

typedef struct {
    uint8_t buf[XCP_TL_HEADER_LEN + XCP_TL_MAX_PACKET];
    uint16_t have;     /* bytes assembled so far                   */
    uint16_t need;     /* total bytes of the frame once LEN known  */
} xcp_tl_assembler_t;

/* Feed one received byte. Returns the packet length (>0) when a complete
 * frame is assembled — the packet then sits at asm->buf + XCP_TL_HEADER_LEN —
 * and 0 otherwise. Oversized frames reset the assembler. */
static inline uint16_t xcp_tl_feed(xcp_tl_assembler_t *a, uint8_t byte) {
    if (a->have < sizeof a->buf) {
        a->buf[a->have] = byte;
    }
    a->have++;
    if (a->have == 2) {
        const uint16_t len = (uint16_t) (a->buf[0] | (a->buf[1] << 8));
        if (len == 0 || len > XCP_TL_MAX_PACKET) {
            a->have = 0; /* resync */
            return 0;
        }
        a->need = (uint16_t) (XCP_TL_HEADER_LEN + len);
    }
    if (a->have >= XCP_TL_HEADER_LEN && a->have == a->need) {
        const uint16_t len = (uint16_t) (a->need - XCP_TL_HEADER_LEN);
        a->have = 0;
        return len;
    }
    return 0;
}

#endif /* PICODESK_XCP_TRANSPORT_H */
