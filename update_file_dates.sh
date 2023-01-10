#!/bin/bash
BASE=${1:-.}
IFS=$'\n'   # Internal Field Separator, set to avoid splitting on spaces

function ts() {
    # Convert an ISO date string to a timestamp
    date -jf "%Y-%m-%d %H:%M:%S%z" $1 +"%Y%m%d%H%M.%S" 2>/dev/null || date -jf "%Y-%m-%d %H:%M:%SZ" $1 +"%Y%m%d%H%M.%S"
}


for FILE in $(find $BASE -name '*.md'); do
    CREATED="$(grep 'created:' $FILE | sed 's/^.*: //' | sed -r 's/-([0-9]*):([0-9]*)$/-\1\2/')"
    CREATED=$(ts $CREATED)
    UPDATED="$(grep 'updated:' $FILE | sed 's/^.*: //' | sed -r 's/-([0-9]*):([0-9]*)$/-\1\2/')"
    UPDATED=$(ts $UPDATED)

    echo "$FILE"
    touch -t $CREATED $FILE
    touch -mt $UPDATED $FILE
done

unset IFS
