function json = picodesk_extract(slxPath)
%PICODESK_EXTRACT Extract one model's VFB interface as descriptor JSON (MAT-001).
%
%   json = picodesk_extract('/path/to/Model.slx')
%
% Loads the model, compiles its interface, and returns a JSON object with
% base_rate_s, inports, outports (name/data_type/width/slope/bias), the
% compiled internal data types (basis of the MAT-002 fast-loop float check
% on the Python side), the model's data-dictionary closure, and the
% interface catalogue those dictionaries declare.
%
% The model's own directory goes on the MATLAB path for the duration of the
% call: data dictionaries attach by NAME and resolve through the path, so
% without this a dictionary-attached model fails to load at all (G-1).
% Dictionaries opened as a side effect are closed on cleanup so back-to-back
% extractions cannot collide on dictionary state.

[modelDir, modelName] = fileparts(slxPath);
addpath(modelDir);
restorePath = onCleanup(@() rmpath(modelDir));
closeDicts = onCleanup(@() picodesk_close_dictionaries());

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

% Data-dictionary closure (attached dictionary + every referenced one),
% resolved to absolute paths. The Python side hashes these files alongside
% the .slx so a dictionary edit invalidates the cache (GUI-001, G-4), and
% checks ports against the declared interface catalogue (G-7).
[info.dictionaries, info.interface_catalog] = ...
    picodesk_dictionary_closure(modelName);

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

function picodesk_close_dictionaries()
try
    Simulink.data.dictionary.closeAll('-discard');
catch
    % nothing open
end
end

function [files, catalog] = picodesk_dictionary_closure(modelName)
files = {};
catalog = {};
attached = get_param(modelName, 'DataDictionary');
if isempty(attached)
    return
end
pending = {attached};
seen = containers.Map('KeyType', 'char', 'ValueType', 'logical');
while ~isempty(pending)
    name = pending{1};
    pending(1) = [];
    if isKey(seen, name)
        continue
    end
    seen(name) = true;
    resolved = which(name);
    if isempty(resolved)
        % Unresolvable reference: report the bare name; the Python side
        % turns a missing file into a per-model diagnosis, not a crash.
        files{end + 1} = name; %#ok<AGROW>
        continue
    end
    files{end + 1} = resolved; %#ok<AGROW>
    dd = Simulink.data.dictionary.open(name);
    pending = [pending, dd.DataSources]; %#ok<AGROW>
    catalog = [catalog, picodesk_catalog_entries(dd, name)]; %#ok<AGROW>
end
end

function entries = picodesk_catalog_entries(dd, dictName)
% Simulink.Signal entries are the declared interface contract; parameters
% are reported too so the host can warn when they will be inlined (G-8).
entries = {};
sec = getSection(dd, 'Design Data');
found = find(sec); %#ok<GTARG>
for i = 1:numel(found)
    entry = found(i);
    try
        value = getValue(entry);
    catch
        continue
    end
    e = struct();
    e.name = entry.Name;
    e.dictionary = dictName;
    e.class = class(value);
    if isprop(value, 'DataType')
        e.data_type = char(value.DataType);
    else
        e.data_type = '';
    end
    if strcmp(e.class, 'Simulink.Parameter') || strcmp(e.class, 'Simulink.Signal')
        entries{end + 1} = e; %#ok<AGROW>
    end
end
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
