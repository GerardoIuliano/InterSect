(set-info :smt-lib-version 2.6)
(set-logic QF_S)
(set-info :license "https://creativecommons.org/licenses/by/4.0/")
(set-info :category "industrial")
(set-info :status sat)

(declare-const X String)
(assert (str.in_re X (str.to_re "httphostHost:Agent\u{22}\u{a}")))
(assert (not (str.in_re X (re.++ (str.to_re "R") ((_ re.loop 8 8) (re.range "0" "9")) (str.to_re "\u{a}")))))
(assert (not (str.in_re X (re.++ (re.union (re.++ (str.to_re "18") (re.range "5" "9") (re.range "0" "9")) (re.++ (re.union (str.to_re "19") (str.to_re "20")) ((_ re.loop 2 2) (re.range "0" "9"))) (str.to_re "2100")) (str.to_re "\u{a}")))))
(check-sat)

(exit)
