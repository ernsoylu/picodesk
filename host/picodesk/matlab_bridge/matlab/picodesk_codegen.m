function json = picodesk_codegen(slxPath, outDir)
%PICODESK_CODEGEN Generate ERT code for one model (MAT-002 / MAT-003).
%
%   json = picodesk_codegen('/path/Model.slx', '/path/out')
%
% Configures Embedded Coder for a bare, RTE-owned target: no generated
% main (the RTE schedules), no non-finite support, integer-only code, and
% output+update combined into a single step function. Returns JSON with the
% generated directory and source list so the Python side can arrange the
% files without guessing paths.
%
% Every model is generated with the SAME configuration on purpose.
% PurelyIntegerCode would be the obvious way to enforce MAT-002 here, but
% it changes the emitted rtwtypes.h, so mixing it across models leaves the
% integer-only and float-capable headers mutually incompatible. MAT-002 is
% instead enforced twice on evidence rather than configuration: the
% extractor rejects float in a fast-loop model before codegen, and
% ert_integrate scans the generated C for real_T/real32_T afterwards.

[~, modelName] = fileparts(slxPath);
if ~exist(outDir, 'dir'); mkdir(outDir); end

previousDir = pwd;
restoreDir = onCleanup(@() cd(previousDir));
cd(outDir);

load_system(slxPath);
cleanup = onCleanup(@() picodesk_close(modelName));

set_param(modelName, 'SystemTargetFile', 'ert.tlc');
set_param(modelName, 'GenCodeOnly', 'on');
set_param(modelName, 'GenerateSampleERTMain', 'off');
set_param(modelName, 'SupportNonFinite', 'off');
set_param(modelName, 'CombineOutputUpdateFcns', 'on');
set_param(modelName, 'SuppressErrorStatus', 'on');
set_param(modelName, 'GenerateReport', 'off');
set_param(modelName, 'RTWVerbose', 'off');

slbuild(modelName);

genDir = fullfile(outDir, [modelName '_ert_rtw']);
% Report only where the code landed. Listing the directory is left to the
% caller: MATLAB cell -> JSON marshalling of file lists is fragile, and
% Python can glob the same directory without ambiguity.
info = struct();
info.model = modelName;
info.dir = genDir;
json = jsonencode(info);
end

function picodesk_close(modelName)
try
    close_system(modelName, 0);
catch
    % already closed
end
end
