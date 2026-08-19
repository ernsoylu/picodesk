*** Comments ***
Shared telemetry parsing for the Renode suites.

The firmware prints one machine-parseable line per second (BLD-003), and the
generated firmware's line is not even fixed in shape — its fields depend on how
many rate groups and debug signals the workspace has.

Parsing that by positional regex group is a trap. Insert a field and every
later group index silently shifts by one: the suite keeps passing, but each
assertion is now checking a different number than it names. Nothing fails, and
nothing tells you. So fields are matched by NAME here, once, and every suite
uses this keyword.

*** Variables ***
# One name=value pair. Field names are C identifiers; values are signed
# decimals or 0x-prefixed hex (the FAULT record prints pc/lr/psr as 0x%08lx).
# int(v, 0) decodes both — safe because no firmware printf zero-pads a
# decimal, which is the one form base-0 rejects.
${TELEMETRY_FIELD_RE}         (?P<k>\\w+)=(?P<v>0x[0-9a-fA-F]+|-?\\d+)

*** Keywords ***
Parse Telemetry Fields
    [Documentation]           Turn one telemetry line into a name -> int dict.
    [Arguments]               ${line}
    ${t}=                     Evaluate
    ...                       {m.group('k'): int(m.group('v'), 0) for m in re.finditer(r"${TELEMETRY_FIELD_RE}", $line)}
    ...                       re
    RETURN                    ${t}

Telemetry Must Contain
    [Documentation]           Guard against a line that parsed to nothing
    ...                       useful — an empty dict would otherwise make every
    ...                       later lookup fail with an unhelpful KeyError.
    [Arguments]               ${t}    ${line}    @{keys}
    FOR    ${key}    IN    @{keys}
        Should Be True        '${key}' in $t
        ...                   msg=telemetry has no ${key}: ${line}
    END
