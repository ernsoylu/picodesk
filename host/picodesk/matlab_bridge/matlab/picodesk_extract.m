function json = picodesk_extract(slxPath)
%PICODESK_EXTRACT Extract one model's VFB interface as descriptor JSON (MAT-001).
%
%   json = picodesk_extract('/path/to/Model.slx')
%
% Loads the model, compiles its interface, and returns a JSON object with
% base_rate_s, inports, outports (name/data_type/width/slope/bias), and the
% compiled internal data types (basis of the MAT-002 fast-loop float check
% on the Python side). Runs inside the persistent engine session (GUI-002).

[~, modelName] = fileparts(slxPath);
load_system(slxPath);

% Compile to resolve port data types and sample times. A single cleanup
% object terminates the compile BEFORE closing — two separate onCleanup
% locals destruct in unspecified order and close_system fails on a model
% that is still compiled.
feval(modelName, [], [], [], 'compile');
cleanup = onCleanup(@() picodesk_term_and_close(modelName));

info = struct();
info.base_rate_s = picodesk_base_rate(modelName);
info.inports = picodesk_ports(modelName, 'Inport');
info.outports = picodesk_ports(modelName, 'Outport');
info.internal_types = picodesk_internal_types(modelName);

json = jsonencode(info);
end

function picodesk_term_and_close(modelName)
try
    feval(modelName, [], [], [], 'term');
catch
    % already terminated
end
close_system(modelName, 0);
end

function rate = picodesk_base_rate(modelName)
sampleTimes = Simulink.BlockDiagram.getSampleTimes(modelName);
periods = [];
for i = 1:numel(sampleTimes)
    v = sampleTimes(i).Value(1);
    if isfinite(v) && v > 0
        periods(end + 1) = v; %#ok<AGROW>
    end
end
rate = min(periods);
end

function ports = picodesk_ports(modelName, blockType)
blocks = find_system(modelName, 'SearchDepth', 1, 'BlockType', blockType);
ports = cell(1, numel(blocks));
for i = 1:numel(blocks)
    block = blocks{i};
    p = struct();
    [~, p.name] = fileparts(block);
    p.name = matlab.lang.makeValidName(p.name);
    if strcmp(blockType, 'Inport')
        hp = get_param(block, 'PortHandles'); h = hp.Outport(1);
    else
        hp = get_param(block, 'PortHandles'); h = hp.Inport(1);
    end
    dt = get_param(h, 'CompiledPortDataType');
    dims = get_param(h, 'CompiledPortWidth');
    [p.data_type, p.slope, p.bias] = picodesk_map_type(dt);
    p.width = double(dims);
    ports{i} = p;
end
ports = [ports{:}];
if isempty(ports)
    ports = struct('name', {}, 'data_type', {}, 'width', {}, ...
                   'slope', {}, 'bias', {});
end
end

function [name, slope, bias] = picodesk_map_type(dt)
slope = 1; bias = 0;
switch dt
    case {'boolean', 'int8', 'uint8', 'int16', 'uint16', ...
          'int32', 'uint32', 'single', 'double'}
        name = dt;
    otherwise
        % Fixed-point alias (e.g. sfix16_En15): resolve storage + scaling.
        t = fixdt(dt);
        if t.Signed
            name = sprintf('int%d', t.WordLength);
        else
            name = sprintf('uint%d', t.WordLength);
        end
        slope = t.Slope;
        bias = t.Bias;
end
end

function types = picodesk_internal_types(modelName)
% Coarse but conservative: every compiled block-output data type in use.
handles = find_system(modelName, 'FindAll', 'on', 'Type', 'port');
seen = containers.Map('KeyType', 'char', 'ValueType', 'logical');
for i = 1:numel(handles)
    dt = get_param(handles(i), 'CompiledPortDataType');
    if ~isempty(dt)
        seen(char(dt)) = true;
    end
end
types = keys(seen);
end
