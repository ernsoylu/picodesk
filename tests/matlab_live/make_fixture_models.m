function make_fixture_models(outDir)
%MAKE_FIXTURE_MODELS Build the Phase 4 reference .slx fixtures (MAT-001).
%
% Creates, into outDir:
%   good/FastCtrl.slx      1 ms fixed-point control loop (int16/uint16)
%   good/ThermalModel.slx  100 ms loop with internal double (allowed)
%   bad/TorqueArbBad.slx   1 ms loop with a double inport (MAT-002 trigger)

goodDir = fullfile(outDir, 'good');
badDir = fullfile(outDir, 'bad');
if ~exist(goodDir, 'dir'); mkdir(goodDir); end
if ~exist(badDir, 'dir'); mkdir(badDir); end

make_fastctrl(goodDir);
make_thermal(goodDir);
make_torquearb_bad(badDir);
end

function configure(model, stepSize)
set_param(model, 'SolverType', 'Fixed-step', ...
          'Solver', 'FixedStepDiscrete', 'FixedStep', stepSize);
end

function make_fastctrl(outDir)
model = 'FastCtrl';
new_system(model);
configure(model, '0.001');

add_block('simulink/Sources/In1', [model '/adc_u'], ...
    'OutDataTypeStr', 'uint16', 'SampleTime', '0.001');
add_block('simulink/Sources/In1', [model '/derate_in'], ...
    'OutDataTypeStr', 'uint8', 'SampleTime', '0.001');
add_block('simulink/Signal Attributes/Data Type Conversion', ...
    [model '/to_i16_a'], 'OutDataTypeStr', 'int16');
add_block('simulink/Signal Attributes/Data Type Conversion', ...
    [model '/to_i16_b'], 'OutDataTypeStr', 'int16');
% A setpoint makes this a closed loop rather than a pure pass-through:
% without it the model is entirely reactive, and with a zero input (an
% unstimulated ADC in emulation) the whole chain rests at zero, which
% proves nothing about the cross-core round trip.
add_block('simulink/Sources/Constant', [model '/setpoint'], ...
    'Value', '5000', 'OutDataTypeStr', 'int16');
add_block('simulink/Math Operations/Sum', [model '/sum'], ...
    'Inputs', '++-', 'OutDataTypeStr', 'int16', ...
    'SaturateOnIntegerOverflow', 'on');
add_block('simulink/Math Operations/Gain', [model '/gain'], ...
    'Gain', '3', 'OutDataTypeStr', 'int16', ...
    'ParamDataTypeStr', 'int16', 'SaturateOnIntegerOverflow', 'on');
add_block('simulink/Sinks/Out1', [model '/torque_cmd']);

add_line(model, 'adc_u/1', 'to_i16_a/1');
add_line(model, 'derate_in/1', 'to_i16_b/1');
add_line(model, 'setpoint/1', 'sum/1');
add_line(model, 'to_i16_a/1', 'sum/2');
add_line(model, 'to_i16_b/1', 'sum/3');
add_line(model, 'sum/1', 'gain/1');
add_line(model, 'gain/1', 'torque_cmd/1');

save_system(model, fullfile(outDir, [model '.slx']));
close_system(model, 0);
end

function make_thermal(outDir)
model = 'ThermalModel';
new_system(model);
configure(model, '0.1');

add_block('simulink/Sources/In1', [model '/load_in'], ...
    'OutDataTypeStr', 'int16', 'SampleTime', '0.1');
add_block('simulink/Math Operations/Gain', [model '/thermal_gain'], ...
    'Gain', '0.017', 'OutDataTypeStr', 'double');
add_block('simulink/Signal Attributes/Data Type Conversion', ...
    [model '/to_u8'], 'OutDataTypeStr', 'uint8', ...
    'SaturateOnIntegerOverflow', 'on');
add_block('simulink/Sinks/Out1', [model '/derate_pct']);

add_line(model, 'load_in/1', 'thermal_gain/1');
add_line(model, 'thermal_gain/1', 'to_u8/1');
add_line(model, 'to_u8/1', 'derate_pct/1');

save_system(model, fullfile(outDir, [model '.slx']));
close_system(model, 0);
end

function make_torquearb_bad(outDir)
model = 'TorqueArbBad';
new_system(model);
configure(model, '0.001');

add_block('simulink/Sources/In1', [model '/trq_a'], ...
    'OutDataTypeStr', 'double', 'SampleTime', '0.001');
add_block('simulink/Signal Attributes/Data Type Conversion', ...
    [model '/to_i16'], 'OutDataTypeStr', 'int16', ...
    'SaturateOnIntegerOverflow', 'on');
add_block('simulink/Sinks/Out1', [model '/trq_out']);

add_line(model, 'trq_a/1', 'to_i16/1');
add_line(model, 'to_i16/1', 'trq_out/1');

save_system(model, fullfile(outDir, [model '.slx']));
close_system(model, 0);
end
