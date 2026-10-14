(set-info :smt-lib-version 2.6)
(set-logic QF_S)
(set-info :license "https://creativecommons.org/licenses/by/4.0/")
(set-info :category "industrial")
(set-info :status sat)

(declare-const X String)
(assert (not (str.in_re X (re.++ (re.union (re.++ (str.to_re " ") (re.range "1" "9")) (re.range "1" "9") (re.++ (str.to_re "0") (re.range "1" "9")) (str.to_re "10") (str.to_re "11") (str.to_re "12")) (re.range "0" "5") (re.range "0" "9") (str.to_re "\u{a}")))))
(check-sat)

(exit)
