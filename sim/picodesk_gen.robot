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
Resource                      ${CURDIR}${/}telemetry.resource

*** Variables ***
${FIRMWARE}                   ${CURDIR}${/}..${/}build-gen${/}picodesk_gen_firmware.elf

*** Keywords ***
Prepare Machine
    Execute Command           $global.FIRMWARE=@${FIRMWARE}
    Execute Script            ${CURDIR}${/}picodesk_sim.resc
    Create Terminal Tester    sysbus.uart0    timeout=30    defaultPauseEmulation=true

Wait For Gen Telemetry
    ${line}=                  Wait For Line On Uart    RTEGEN hb=    timeout=30
    ${t}=                     Parse Telemetry Fields    ${line['Line']}
    Telemetry Must Contain    ${t}    ${line['Line']}    hb    ovr    slf
    ...                       slow_100ms    daq    FastCtrl_torque_cmd
    ...                       SlowSense_derate_pct
    RETURN                    ${t}

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
    ${t}=    Wait For Gen Telemetry
    Should Be True            900 <= ${t}[dhb] <= 1100    msg=fast rate off: ${t}[dhb]
    Should Be Equal As Integers    ${t}[ovr]    0    msg=overruns
    Should Be Equal As Integers    ${t}[slf]    0    msg=seqlock faults
    Should Be True            ${t}[slow_100ms] > 0    msg=slow_100ms group not firing
    Should Be True            ${t}[daq] > 0    msg=DAQ not streaming
    Should Be True            ${t}[FastCtrl_torque_cmd] != 0    msg=fast model output dead
    Should Be True            ${t}[SlowSense_derate_pct] > 0
    ...                       msg=cross-core round trip broken
