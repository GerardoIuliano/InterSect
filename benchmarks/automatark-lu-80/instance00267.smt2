(set-info :smt-lib-version 2.6)
(set-logic QF_S)
(set-info :license "https://creativecommons.org/licenses/by/4.0/")
(set-info :category "industrial")
(set-info :status sat)

(declare-const X String)
(assert (not (str.in_re X (re.++ (str.to_re "+86") ((_ re.loop 8 8) (re.range "0" "9")) (str.to_re "\u{a}1") (re.union (re.++ (str.to_re "3") (re.range "0" "9")) (str.to_re "45") (str.to_re "47") (re.++ (str.to_re "5") (re.union (re.range "0" "3") (str.to_re ",") (re.range "5" "9"))) (re.++ (str.to_re "8") (re.union (str.to_re "0") (str.to_re ",") (str.to_re "2") (re.range "5" "9"))))))))
(check-sat)

(exit)
