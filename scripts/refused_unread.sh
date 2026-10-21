# The R3 check of scripts/smoke.sh (phase 04 review round 2, finding 2; ADR-0271), sourced by it:
# an upload over the size limit is answered with the JSON 413 file_too_large before its body is
# read (the answer arrives while the client has sent less than the body; a gateway that read the
# body first could answer only after all of it), with no reset (curl exits 0). Each condition fails
# the check on its own. `refused_unread URL TOKEN FILE OUT` prints "ok <sent> <size>" or
# "fail <why>" and returns 0 or 1; the answer goes to OUT.
refused_unread() {
  local url=$1 token=$2 file=$3 out=$4 size result rc code sent
  size=$(( $(wc -c < "$file") ))
  result=$(curl -s --expect100-timeout 10 -o "$out" -w '%{http_code} %{size_upload}' \
    -H "Authorization: Bearer $token" -F "file=@$file" "$url"); rc=$?
  (( rc == 0 )) || { echo "fail the connection was reset or broken (curl exit $rc)"; return 1; }
  read -r code sent <<< "$result"
  [[ $code == 413 ]] || { echo "fail HTTP $code: $(head -c 200 "$out")"; return 1; }
  jq -e '.error.code == "file_too_large"' "$out" >/dev/null 2>&1 \
    || { echo "fail no JSON 413 file_too_large: $(head -c 200 "$out")"; return 1; }
  (( ${sent%.*} < size )) || { echo "fail the whole body was sent ($sent of $size bytes) before the answer"; return 1; }
  echo "ok ${sent%.*} $size"
}
