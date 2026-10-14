(set-info :smt-lib-version 2.6)
(set-logic QF_S)
(set-info :license "https://creativecommons.org/licenses/by/4.0/")
(set-info :category "industrial")
(set-info :status sat)

(declare-const X String)
(assert (not (str.in_re X (re.++ ((_ re.loop 6 7) (re.range "0" "9")) (str.to_re "\u{a}(0") ((_ re.loop 2 2) (re.range "0" "9")) (str.to_re ")")))))
(check-sat)

(exit)
