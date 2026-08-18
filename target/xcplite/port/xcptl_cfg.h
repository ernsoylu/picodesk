#pragma once

/* xcptl_cfg.h — transport layer sizing for XCP over USB CDC-ACM.
 *
 * The transport layer *type* stays XCP_TRANSPORT_LAYER_ETH even though the
 * wire is a CDC serial pipe. That is not a fudge: the Ethernet TL's frame
 * header is `uint16 LEN | uint16 CTR | packet`, byte-for-byte the same as
 * pyxcp's SxI HEADER_LEN_CTR_WORD framing that target/xcp/xcp_transport.h
 * already implements and that the existing host tooling speaks. Selecting
 * ETH keeps the protocol core emitting exactly that header; selecting CAN
 * would force MAX_CTO == 8 and disable commands this system uses.
 *
 * Sizes are cut down from upstream's Ethernet MTU defaults (segment 1472,
 * CTO 252) to a full-speed CDC bulk packet, so one XCP message is one USB
 * packet and no segmentation shim is needed.
 */

#define XCP_TRANSPORT_LAYER_TYPE XCP_TRANSPORT_LAYER_ETH
#define XCP_TRANSPORT_LAYER_VERSION 0x0104

/* Command responses go through the transmit queue, so CRM and DTO share one
 * counter sequence and the queue is drained before DAQ stops. */
#define XCPTL_QUEUED_CRM

/* Fixed by the protocol: uint16 LEN | uint16 CTR. */
#define XCPTL_TRANSPORT_LAYER_HEADER_SIZE 4

/* One USB full-speed bulk packet carries header + payload. Both CTO and DTO
 * must be a multiple of 4. */
#define XCPTL_MAX_SEGMENT_SIZE 68
#define XCPTL_MAX_DTO_SIZE (XCPTL_MAX_SEGMENT_SIZE - XCPTL_TRANSPORT_LAYER_HEADER_SIZE)
#define XCPTL_MAX_CTO_SIZE 64
#define XCPTL_PACKET_ALIGNMENT 4

/* PicoDesk supplies its own transmit queue in picodesk_xcp_tl.c (SRAM2,
 * BLD-002), so upstream's queue array is not built. These remain defined
 * because the protocol core references them for flush timing. */
#define XCPTL_QUEUE_SIZE 8
#define XCPTL_QUEUE_TRANSMIT_CYCLE_TIME (1 * CLOCK_TICKS_PER_MS)
#define XCPTL_QUEUE_FLUSH_CYCLE_MS 50
