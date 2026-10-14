(set-info :smt-lib-version 2.6)
(set-logic QF_S)
(set-info :license "https://creativecommons.org/licenses/by/4.0/")
(set-info :category "industrial")
(set-info :status sat)

(declare-const X String)
(assert (not (str.in_re X (re.++ (str.to_re "Subject:") (re.* re.allchar) (str.to_re "Host:") (re.* re.allchar) (str.to_re "/products/spyblocs/\u{13}") (re.union (str.to_re " ") (str.to_re "\u{9}") (str.to_re "\u{a}") (str.to_re "\u{c}") (str.to_re "\u{d}")) (str.to_re "internalcyber@yahoo.comHeadersRedirection\u{a}")))))
(assert (not (str.in_re X (re.++ (re.union (re.++ (str.to_re "0") (re.union (str.to_re "1") (str.to_re ".") (str.to_re "9"))) (re.++ (str.to_re "1") (re.range "0" "9")) (re.++ (str.to_re "2") (re.range "0" "9")) (re.++ (str.to_re "3") (re.union (str.to_re "0") (str.to_re ".") (str.to_re "1"))) (str.to_re "1") (str.to_re ".") (str.to_re "9")) re.allchar (re.union (re.range "0" "9") (re.++ (str.to_re "0") (re.range "0" "9")) (re.++ (str.to_re "1") (re.union (str.to_re "0") (str.to_re ".") (str.to_re "2")))) re.allchar ((_ re.loop 4 4) (re.range "0" "9")) (str.to_re "\u{a}")))))
(assert (not (str.in_re X (re.++ (str.to_re "search.dropspam.com") (re.* re.allchar) (str.to_re "SupportFiles\u{13}") (re.* re.allchar) (str.to_re "Referer:\u{a}")))))
(check-sat)

(exit)
