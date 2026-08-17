*** Comments ***
Renode system test for GENERATED firmware (Phase 5, milestone M1).
The RTE under test was produced by picodesk.rtegen from the fixture
workspace (tests/fixtures/gen_ws): two cross-wired ASW models (full mesh,
both directions through bounded seqlock buses) plus a HAL ADC binding.
Telemetry line:
  RTEGEN hb=<n> dhb=<n> exec_max=<n> ovr=<n> slf=<n> slow_100ms=<n>
         daq=<n> daq_drain=<n> FastCtrl_torque_cmd=<n> SlowSense_derate_pct=<n>

*** Settings ***
Suite Setup                   Setup
Suite Teardown                Teardown
Test Teardown                 Test Teardown
Resource                      ${RENODEKEYWORDS}

*** Variables ***
${FIRMWARE}                   ${CURDIR}${/}..${/}build-gen${/}picodesk_gen_firmware.elf
${GEN_RE}                     SEPARATOR=
...                           RTEGEN hb=(\\d+) dhb=(\\d+) exec_max=(\\d+) ovr=(\\d+)${SPACE}
...                           slf=(\\d+) slow_100ms=(\\d+) daq=(\\d+) daq_drain=(\\d+)${SPACE}
...                           FastCtrl_torque_cmd=(-?\\d+) SlowSense_derate_pct=(-?\\d+)

*** Keywords ***
Prepare Machine
    Execute Command           $global.FIRMWARE=@${FIRMWARE}
    Execute Script            ${CURDIR}${/}picodesk_sim.resc
    Create Terminal Tester    sysbus.uart0    timeout=30    defaultPauseEmulation=true

Wait For Gen Telemetry
    ${line}=                  Wait For Line On Uart    RTEGEN hb=    timeout=30
    ${m}=                     Evaluate    re.search(r"${GEN_RE}", $line['Line'])    re
    Should Not Be Equal       ${m}    ${None}    msg=unparseable: ${line['Line']}
    RETURN                    ${m}

*** Test Cases ***
Generated RTE Runs The Full Mesh
    [Documentation]           Generated dispatcher boots (RTE-002), both
    ...                       seqlock bus directions stay healthy (RTE-004),
    ...                       DAQ streams (RTE-005), and the ASW<->ASW loop
    ...                       closes: the slow model's derate output — fed by
    ...                       the fast model's torque through one bus — comes
    ...                       back through the other bus and is nonzero.
    Prepare Machine
    Wait For Gen Telemetry
    Wait For Gen Telemetry
    ${m}=    Wait For Gen Telemetry
    ${dhb}=      Evaluate     int($m.group(2))
    ${ovr}=      Evaluate     int($m.group(4))
    ${slf}=      Evaluate     int($m.group(5))
    ${s100}=     Evaluate     int($m.group(6))
    ${daq}=      Evaluate     int($m.group(7))
    ${torque}=   Evaluate     int($m.group(9))
    ${derate}=   Evaluate     int($m.group(10))
    Should Be True            900 <= ${dhb} <= 1100    msg=fast rate off: ${dhb}
    Should Be Equal As Integers    ${ovr}    0    msg=overruns
    Should Be Equal As Integers    ${slf}    0    msg=seqlock faults
    Should Be True            ${s100} > 0    msg=slow_100ms group not firing
    Should Be True            ${daq} > 0    msg=DAQ not streaming
    Should Be True            ${torque} != 0    msg=fast model output dead
    Should Be True            ${derate} > 0    msg=cross-core round trip broken
