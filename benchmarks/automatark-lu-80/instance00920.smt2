(set-info :smt-lib-version 2.6)
(set-logic QF_S)
(set-info :license "https://creativecommons.org/licenses/by/4.0/")
(set-info :category "industrial")
(set-info :status sat)

(declare-const X String)
(assert (not (str.in_re X (re.++ (str.to_re "\u{a}$") (re.opt (re.range "0" "9")) (re.opt (re.range "0" "9")) (re.+ (re.union (re.range "a" "z") (re.range "A" "Z")))))))
(check-sat)

(exit)
