/* TinyUSB device configuration: one CDC-ACM interface dedicated to XCP
 * (CAL-001). stdio stays on UART so the XCP channel owns the CDC pipe.
 */

#ifndef PICODESK_TUSB_CONFIG_H
#define PICODESK_TUSB_CONFIG_H

#define CFG_TUSB_RHPORT0_MODE OPT_MODE_DEVICE

#ifndef CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_SECTION
#endif
#ifndef CFG_TUSB_MEM_ALIGN
#define CFG_TUSB_MEM_ALIGN __attribute__((aligned(4)))
#endif

#define CFG_TUD_ENDPOINT0_SIZE 64

#define CFG_TUD_CDC 1
#define CFG_TUD_MSC 0
#define CFG_TUD_HID 0
#define CFG_TUD_MIDI 0
#define CFG_TUD_VENDOR 0

/* Sized for sustained DAQ streaming (>= 50 signals @ 100 Hz, CAL-001). */
#define CFG_TUD_CDC_RX_BUFSIZE 256
#define CFG_TUD_CDC_TX_BUFSIZE 1024
#define CFG_TUD_CDC_EP_BUFSIZE 64

#endif /* PICODESK_TUSB_CONFIG_H */
