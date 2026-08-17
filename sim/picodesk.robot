*** Comments ***
Renode system tests for the PicoDesk spike firmware (Phases 0-3).
Runs the real UF2-equivalent ELF (PICODESK_SIM: USB transport stubbed, USB is
not modeled) on the emulated dual-core RP2040 and asserts on the firmware's
own telemetry stream:
  RTE hb=<n> dhb=<n> exec_max=<n> jit_max=<n> ovr=<n> slf=<n> r10=<n>
      r100=<n> daq=<n> daq_depth=<n> daq_drop=<n> cal_sw=<n> cal_kp=<n> ...

Phase coverage:
  P0/P1  boot, FreeRTOS SMP on both cores, 1 kHz fast dispatch (RTE-002)
  P2     seqlock health (RTE-004), coherent DAQ ring flow (RTE-005)
  P3     CAL page transactional switch at the step boundary (RTE-003)

*** Settings ***
Suite Setup                   Setup
Suite Teardown                Teardown
Test Teardown                 Test Teardown
Resource                      ${RENODEKEYWORDS}

*** Variables ***
${FIRMWARE}                   ${CURDIR}${/}..${/}build-sim${/}picodesk_firmware.elf
${TELEMETRY_RE}               SEPARATOR=
...                           RTE hb=(\\d+) dhb=(\\d+) exec_max=(\\d+) jit_max=(\\d+)${SPACE}
...                           ovr=(\\d+) slf=(\\d+) r10=(\\d+) r100=(\\d+) daq=(\\d+)${SPACE}
...                           daq_depth=(\\d+) daq_drop=(\\d+) cal_sw=(\\d+) cal_kp=(-?\\d+)

*** Keywords ***
Prepare Machine
    Execute Command           $global.FIRMWARE=@${FIRMWARE}
    Execute Script            ${CURDIR}${/}picodesk_sim.resc
    Create Terminal Tester    sysbus.uart0    timeout=30    defaultPauseEmulation=true

Wait For Telemetry
    [Documentation]           Returns the parsed groups of the next RTE line.
    ${line}=                  Wait For Line On Uart    RTE hb=    timeout=30
    ${m}=                     Evaluate    re.search(r"${TELEMETRY_RE}", $line['Line'])    re
    Should Not Be Equal       ${m}    ${None}    msg=unparseable telemetry: ${line['Line']}
    RETURN                    ${m}

*** Test Cases ***
Boot And 1kHz Fast Dispatch
    [Documentation]           P0/P1: SMP boot on both cores; the core 0 timer
    ...                       ISR ticks at 1 kHz with zero overruns (RTE-002).
    Prepare Machine
    Wait For Telemetry
    ${m}=    Wait For Telemetry
    ${dhb}=    Evaluate       int($m.group(2))
    Should Be True            900 <= ${dhb} <= 1100    msg=fast tick rate off: dhb=${dhb}
    ${ovr}=    Evaluate       int($m.group(5))
    Should Be Equal As Integers    ${ovr}    0    msg=fast-loop overruns detected

Rate Groups And RTE Primitives Healthy
    [Documentation]           P1/P2: 10 ms and 100 ms rate groups fire at 1/10
    ...                       and 1/100 of the fast rate (RTE-002); cross-core
    ...                       seqlocks never exhaust retries (RTE-004); the DAQ
    ...                       ring streams coherently without drops (RTE-005).
    Prepare Machine
    Wait For Telemetry
    Wait For Telemetry
    ${m}=    Wait For Telemetry
    ${hb}=      Evaluate      int($m.group(1))
    ${slf}=     Evaluate      int($m.group(6))
    ${r10}=     Evaluate      int($m.group(7))
    ${r100}=    Evaluate      int($m.group(8))
    ${daq}=     Evaluate      int($m.group(9))
    ${drop}=    Evaluate      int($m.group(11))
    Should Be Equal As Integers    ${slf}    0    msg=seqlock stale fallbacks: ${slf}
    Should Be True            ${r10} > 0 and ${hb}/15 <= ${r10} <= ${hb}/7
    ...                       msg=10ms rate group off: r10=${r10} hb=${hb}
    Should Be True            ${r100} > 0 and ${hb}/150 <= ${r100} <= ${hb}/70
    ...                       msg=100ms rate group off: r100=${r100} hb=${hb}
    Should Be True            ${daq} > 0    msg=DAQ ring not draining
    Should Be Equal As Integers    ${drop}    0    msg=DAQ frames dropped: ${drop}

CAL Page Transactional Switch
    [Documentation]           P2/P3: the stats task edits the offline page and
    ...                       arms SET_CAL_PAGE-style switches; the fast ISR
    ...                       commits them at step boundaries and the new
    ...                       parameter set becomes active atomically (RTE-003).
    ...                       The switch is armed on the 5th one-second stats
    ...                       iteration, so poll telemetry lines until it lands.
    Prepare Machine
    ${sw}=    Set Variable    ${0}
    ${kp}=    Set Variable    ${0}
    FOR    ${i}    IN RANGE    10
        ${m}=    Wait For Telemetry
        ${ovr}=    Evaluate   int($m.group(5))
        Should Be Equal As Integers    ${ovr}    0    msg=overruns during CAL switching
        ${sw}=    Evaluate    int($m.group(12))
        ${kp}=    Evaluate    int($m.group(13))
        Exit For Loop If      ${sw} > 0
    END
    Should Be True            ${sw} >= 1    msg=no CAL page switch committed
    Should Be Equal As Integers    ${kp}    11469    msg=switched page not active: kp=${kp}
