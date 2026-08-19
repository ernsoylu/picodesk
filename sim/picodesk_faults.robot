*** Comments ***
Fault-injection drills on the emulated RP2040 (Phase 6).

Each drill injects a real fault, lets the chip reset, and reads the
post-mortem the next boot prints:

  BLD-006  fault record written to .noinit_fault, surviving the watchdog
           reset, reported on the next boot as
             FAULT kind=<n> pc=0x.. lr=0x.. psr=0x.. core=<n> hb=<n>
                   seq=<n> boot_wdt=<0|1>
  BLD-007  cross-core watchdog: a wedged core 0 fast path stops the
           heartbeat; the core 1 monitor withholds the feed and the
           hardware watchdog resets the chip even though core 1 is healthy

Injection console (target/src/fault_inject.c), one char over the UART:
  h = HardFault   a = assert   w = wedge fast ISR   m = stall monitor

NOT COVERED HERE — hardware-only gate: HardFault *exception dispatch*.
Renode 1.16.1's Cortex-M model halts the core with "CPU abort" on a fetch
from unmapped memory instead of vectoring to the HardFault handler, so the
naked handler's stacked-frame extraction cannot be exercised in emulation.
Everything downstream of it — record write, watchdog reboot, persistence
across reset, boot report — is covered by the assert drill, which shares
that entire path. The 'h' injection remains for the on-hardware drill.

*** Settings ***
Suite Setup                   Setup
Suite Teardown                Teardown
Test Teardown                 Test Teardown
Resource                      ${RENODEKEYWORDS}
Resource                      ${CURDIR}${/}telemetry.resource

*** Variables ***
${FIRMWARE}                   ${CURDIR}${/}..${/}build-sim${/}picodesk_firmware.elf
${BOOT_BANNER}                PicoDesk RTE spike boot

*** Keywords ***
Prepare Machine
    Execute Command           $global.FIRMWARE=@${FIRMWARE}
    Execute Script            ${CURDIR}${/}picodesk_sim.resc
    Create Terminal Tester    sysbus.uart0    timeout=60    defaultPauseEmulation=true

Boot And Reach Steady State
    Prepare Machine
    Wait For Line On Uart     ${BOOT_BANNER}
    # A freshly created machine has no surviving record.
    Wait For Line On Uart     FAULT none
    # Telemetry proves the RTE is really running before we break it.
    Wait For Line On Uart     RTE hb=

Inject
    [Arguments]               ${command}
    Execute Command           sysbus.uart0 WriteChar ${command}

Parse Fault Line
    [Documentation]           FAULT record as a name -> int dict; hex fields
    ...                       (pc/lr/psr) decode transparently.
    [Arguments]               ${line}
    ${t}=                     Parse Telemetry Fields    ${line}
    Telemetry Must Contain    ${t}    ${line}    kind    pc    lr    psr
    ...                       core    hb    seq    boot_wdt
    RETURN                    ${t}

*** Test Cases ***
Assert Failure Survives Reset And Is Reported
    [Documentation]           BLD-006: a failed configASSERT records file/line
    ...                       into .noinit_fault, reboots through the
    ...                       watchdog, and the next boot prints the record —
    ...                       proving the section really does survive a reset.
    Boot And Reach Steady State
    Inject                    0x61    # 'a'
    Wait For Line On Uart     INJECT assert
    Wait For Line On Uart     ${BOOT_BANNER}
    ${line}=    Wait For Line On Uart    FAULT kind=
    ${t}=    Parse Fault Line    ${line['Line']}
    Should Be Equal As Integers    ${t}[kind]    2    msg=expected FAULT_KIND_ASSERT
    # pc carries the __FILE__ pointer (flash), lr the line number.
    Should Be True            ${t}[pc] >= 0x10000000    msg=file pointer not in flash
    Should Be True            ${t}[lr] > 0    msg=assert line number not recorded
    # The heartbeat at fault time proves the RTE was live when it died.
    Should Be True            ${t}[hb] > 0    msg=heartbeat not captured
    Should Be Equal As Integers    ${t}[boot_wdt]    1
    ...                       msg=reset was not a watchdog reset

Fault Record Is Consumed After Reporting
    [Documentation]           BLD-006: the record is invalidated once printed,
    ...                       so a later clean boot does not re-report a stale
    ...                       fault as if it were new.
    Boot And Reach Steady State
    Inject                    0x61    # 'a'
    Wait For Line On Uart     INJECT assert
    Wait For Line On Uart     ${BOOT_BANNER}
    Wait For Line On Uart     FAULT kind=
    # Second reset, this time with no fault: the record must read clean.
    Inject                    0x6D    # 'm' — monitor stall -> watchdog reset
    Wait For Line On Uart     INJECT monitor_stall
    Wait For Line On Uart     ${BOOT_BANNER}
    ${line}=    Wait For Line On Uart    FAULT
    Should Contain            ${line['Line']}    FAULT none
    ...                       msg=stale record re-reported after a clean reset

Wedged Fast Path Trips The Cross-Core Watchdog
    [Documentation]           BLD-007: core 0's fast ISR is wedged while
    ...                       core 1 stays healthy. A plain
    ...                       task-feeds-the-watchdog design would keep
    ...                       feeding forever; the heartbeat check must catch
    ...                       the stall and let the chip reset.
    Boot And Reach Steady State
    Inject                    0x77    # 'w'
    Wait For Line On Uart     INJECT isr_wedge
    # Core 1 is demonstrably alive here — it keeps printing telemetry — while
    # the heartbeat delta falls to zero because core 0 is wedged.
    ${stalled}=    Set Variable    ${False}
    FOR    ${i}    IN RANGE    3
        ${line}=    Wait For Line On Uart    RTE hb=
        ${t}=    Parse Telemetry Fields    ${line['Line']}
        ${stalled}=    Evaluate    $stalled or $t.get('dhb') == 0
        Exit For Loop If    ${stalled}
    END
    Should Be True            ${stalled}
    ...                       msg=heartbeat never stalled while core 1 kept running
    Wait For Line On Uart     ${BOOT_BANNER}
    ${line}=    Wait For Line On Uart    FAULT
    Should Contain            ${line['Line']}    boot_wdt=1
    ...                       msg=reset was not caused by the watchdog

Stalled Monitor Task Trips The Watchdog
    [Documentation]           BLD-007: the monitor itself stops feeding (a
    ...                       wedged core 1 task); the hardware watchdog is
    ...                       the backstop.
    Boot And Reach Steady State
    Inject                    0x6D    # 'm'
    Wait For Line On Uart     INJECT monitor_stall
    Wait For Line On Uart     ${BOOT_BANNER}
    ${line}=    Wait For Line On Uart    FAULT
    Should Contain            ${line['Line']}    boot_wdt=1

Healthy System Keeps Feeding The Watchdog
    [Documentation]           BLD-007 negative control: with the fast path
    ...                       ticking, the monitor keeps feeding and the
    ...                       system runs on — the heartbeat advances across
    ...                       several seconds and no reboot banner appears.
    Boot And Reach Steady State
    Wait For Line On Uart     RTE hb=
    ${line}=    Wait For Line On Uart    RTE hb=
    ${t}=    Parse Telemetry Fields    ${line['Line']}
    Telemetry Must Contain    ${t}    ${line['Line']}    dhb
    Should Be True            900 <= ${t}[dhb] <= 1100
    ...                       msg=heartbeat not advancing at 1 kHz: ${t}[dhb]
    Should Not Be On Uart     ${BOOT_BANNER}    timeout=3
