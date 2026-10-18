# select-main.awk: filter src/main.maude for a build configuration.
#
# Only active lines are considered: "load X" and "pr M ." at the start of a line
# (after indentation). Lines starting with "---" are comments and never match.
# Commented-out code, i.e. "--- pr M ." or "--- load X" (a "---" comment whose text is
# one of the commands below), is removed from the output. Other "---" lines (prose
# headers and notes) are kept.
#
#   -v skip_loads="F ..."   drop "load F" lines
#   -v skip_mods="M ..."    drop "pr M ." lines
#   -v without="M ..."      modules requested by WITHOUT; each must match some
#                           active "pr" line, otherwise this is an error
#
# Output: LF line endings (CR is stripped from every input line).

BEGIN {
    n = split(skip_loads, a, " ")
    for (i = 1; i <= n; i++) drop_load[a[i]] = 1
    n = split(skip_mods, b, " ")
    for (i = 1; i <= n; i++) drop_mod[b[i]] = 1
    n = split(without, c, " ")
    for (i = 1; i <= n; i++) want[c[i]] = 1
}

{
    sub(/\r$/, "")
    if ($0 ~ /^[ \t]*---[ \t]+(pr|load|inc|including|extending|protecting)[ \t]/) next
    if ($0 ~ /^[ \t]*load[ \t]+/) {
        if ($2 in drop_load) next
    } else if ($0 ~ /^[ \t]*pr[ \t]+/) {
        if ($2 in want) hit[$2] = 1
        if ($2 in drop_mod) next
    }
    print
}

END {
    status = 0
    for (w in want) {
        if (!(w in hit)) {
            print "select-main.awk: WITHOUT name matches no active pr line: " w > "/dev/stderr"
            status = 1
        }
    }
    exit status
}
