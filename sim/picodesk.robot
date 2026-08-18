*** Comments ***
Renode system tests for the PicoDesk spike firmware (Phases 0-3).
Runs the real UF2-equivalent ELF (PICODESK_SIM: USB transport stubbed, USB is
not modeled) on the emulated dual-core RP2040 and asserts on the firmware's
own telemetry stream:
  RTE hb=<n> dhb=<n> exec_max=<n> jit_max=<n> ovr=<n> slf=<n> crit_max=<n>
      r10=<n> r100=<n> daq=<n> daq_depth=<n> daq_drop=<n> cal_sw=<n>
      cal_kp=<n> hwm10=<n> hwm100=<n>

Fields are matched by NAME, not position. Positional groups silently
mis-assign every later field when one is inserted, and the assertions keep
passing on the wrong numbers.

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
${FIELD_RE}                   (?P<k>\\w+)=(?P<v>-?\\d+)

*** Keywords ***
Prepare Machine
    Execute Command           $global.FIRMWARE=@${FIRMWARE}
    Execute Script            ${CURDIR}${/}picodesk_sim.resc
    Create Terminal Tester    sysbus.uart0    timeout=30    defaultPauseEmulation=true

Wait For Telemetry
    [Documentation]           Returns the next RTE line as a name -> int dict.
    ${line}=                  Wait For Line On Uart    RTE hb=    timeout=30
    ${t}=                     Evaluate
    ...                       {m.group('k'): int(m.group('v')) for m in re.finditer(r"${FIELD_RE}", $line['Line'])}
    ...                       re
    Should Be True            'hb' in $t and 'cal_kp' in $t
    ...                       msg=unparseable telemetry: ${line['Line']}
    RETURN                    ${t}

*** Test Cases ***
Boot And 1kHz Fast Dispatch
    [Documentation]           P0/P1: SMP boot on both cores; the core 0 timer
    ...                       ISR ticks at 1 kHz with zero overruns (RTE-002).
    Prepare Machine
    Wait For Telemetry
    ${t}=    Wait For Telemetry
    Should Be True            900 <= ${t}[dhb] <= 1100
    ...                       msg=fast tick rate off: dhb=${t}[dhb]
    Should Be Equal As Integers    ${t}[ovr]    0    msg=fast-loop overruns detected

Rate Groups And RTE Primitives Healthy
    [Documentation]           P1/P2: 10 ms and 100 ms rate groups fire at 1/10
    ...                       and 1/100 of the fast rate (RTE-002); cross-core
    ...                       seqlocks never exhaust retries (RTE-004); the DAQ
    ...                       ring streams coherently without drops (RTE-005).
    Prepare Machine
    Wait For Telemetry
    Wait For Telemetry
    ${t}=    Wait For Telemetry
    Should Be Equal As Integers    ${t}[slf]    0
    ...                       msg=seqlock stale fallbacks: ${t}[slf]
    Should Be True            ${t}[r10] > 0 and ${t}[hb]/15 <= ${t}[r10] <= ${t}[hb]/7
    ...                       msg=10ms rate group off: r10=${t}[r10] hb=${t}[hb]
    Should Be True            ${t}[r100] > 0 and ${t}[hb]/150 <= ${t}[r100] <= ${t}[hb]/70
    ...                       msg=100ms rate group off: r100=${t}[r100] hb=${t}[hb]
    Should Be True            ${t}[daq] > 0    msg=DAQ ring not draining
    Should Be Equal As Integers    ${t}[daq_drop]    0
    ...                       msg=DAQ frames dropped: ${t}[daq_drop]

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
        ${t}=    Wait For Telemetry
        Should Be Equal As Integers    ${t}[ovr]    0
        ...                   msg=overruns during CAL switching
        ${sw}=    Set Variable    ${t}[cal_sw]
        ${kp}=    Set Variable    ${t}[cal_kp]
        Exit For Loop If      ${sw} > 0
    END
    Should Be True            ${sw} >= 1    msg=no CAL page switch committed
    Should Be Equal As Integers    ${kp}    11469    msg=switched page not active: kp=${kp}
