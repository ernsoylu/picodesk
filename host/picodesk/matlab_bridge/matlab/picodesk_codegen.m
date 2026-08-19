function json = picodesk_codegen(slxPath, outDir, baseRateS)
%PICODESK_CODEGEN Generate ERT code for one model (MAT-002 / MAT-003).
%
%   json = picodesk_codegen('/path/Model.slx', '/path/out')
%   json = picodesk_codegen('/path/Model.slx', '/path/out', 0.01)
%
% Configures Embedded Coder for a bare, RTE-owned target: no generated
% main (the RTE schedules), no non-finite support, and output+update
% combined into a single step function. Returns JSON with the generated
% directory so the Python side can arrange the files without guessing paths.
%
% Every model is generated with the SAME configuration on purpose.
% PurelyIntegerCode would be the obvious way to enforce MAT-002 here, but
% it changes the emitted rtwtypes.h, so mixing it across models leaves the
% integer-only and float-capable headers mutually incompatible. MAT-002 is
% instead enforced twice on evidence rather than configuration: the
% extractor rejects float in a fast-loop model before codegen, and
% ert_integrate scans the generated C for real_T/real32_T afterwards.
%
% Identifier naming is FORCED, not inherited from the model (MAT-003, G-3).
% The Embedded Coder advisor's "RAM efficiency" objective rewrites the
% naming rules to rt$N$M / $N$M, which emits rtU/rtY/ExtU with no model
% prefix — so any two advisor-configured models produce duplicate symbols
% and cannot link, and the RTE adapters bind <Model>_U symbols that do not
% exist. Forcing the factory rules here makes the pipeline independent of
% whatever configuration a model arrives with.
%
% baseRateS (optional, > 0) forces the solver to a fixed discrete step of
% that period before codegen (RTE-002, G-2). This is how an integration-time
% rate assignment becomes real: a rate-agnostic model (FixedStepAuto,
% everything inherited) is generated at exactly the dispatcher slot the
% integrator assigned, so the emitted step function matches its slot.

[modelDir, modelName] = fileparts(slxPath);
if ~exist(outDir, 'dir'); mkdir(outDir); end

previousDir = pwd;
restoreDir = onCleanup(@() cd(previousDir));
cd(outDir);

% Data dictionaries attach by name and resolve through the path (G-1).
addpath(modelDir);
restorePath = onCleanup(@() rmpath(modelDir));
closeDicts = onCleanup(@() picodesk_close_dictionaries());

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

% MAT-003: model-prefixed identifiers, regardless of the model's own rules.
set_param(modelName, 'CustomSymbolStrGlobalVar', '$R$N$M');
set_param(modelName, 'CustomSymbolStrType', '$N$R$M_T');
set_param(modelName, 'CustomSymbolStrField', '$N$M');
set_param(modelName, 'CustomSymbolStrFcn', '$R$N$M$F');

% RTE-002: integration-time rate assignment (G-2).
if nargin >= 3 && baseRateS > 0
    set_param(modelName, 'SolverType', 'Fixed-step');
    set_param(modelName, 'Solver', 'FixedStepDiscrete');
    set_param(modelName, 'FixedStep', num2str(baseRateS, '%.17g'));
end

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

function picodesk_close_dictionaries()
try
    Simulink.data.dictionary.closeAll('-discard');
catch
    % nothing open
end
end
