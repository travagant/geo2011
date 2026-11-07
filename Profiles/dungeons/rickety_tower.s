// Dungeon level 3

id	"Rickety_Tower";

scene	"Resources\\Hexagons\\Models\\Arena\\arena_back_dungeon\\arena_back_dungeon_01_hexmap.scene";

// party
// unit "id" lvlcap;
// or
// class amount "unit_side" level lvlcap;

party						
{						
	melee		"greenskins_giant"	28-40	40	;
}						
						
party						
{						
	melee	2	"greenskins"	5-16	16	;
	melee		"greenskins"	19-30	30	;
}						
						
party						
{						
	melee	2	"greenskins"	1-9	9	;
	melee		"greenskins_giant"	21-33	33	;
}						
						
party						
{						
	melee	2	"greenskins"	1-9	9	;
	melee		"greenskins"	5-16	16	;
	caster		"greenskins"	15-27	27	;
}						
						
party						
{						
	melee	2	"greenskins"	5-16	16	;
	caster		"greenskins"	15-27	27	;
}						
						
party						
{						
	melee		"greenskins"	19-30	30	;
	caster		"greenskins"	15-27	27	;
}						
						
party						
{						
	caster		"greenskins"	15-27	27	;
	caster		"greenskins"	15-27	27	;
}						
						
party						
{						
	caster	2	"greenskins"	15-27	27	;
}						
						
party						
{						
	caster		"greenskins"	15-27	27	;
	range	2	"greenskins"	5-16	16	;
}						
						
party						
{						
	caster		"greenskins"	15-27	27	;
	range		"greenskins"	15-27	27	;
}						
						
party						
{						
	caster		"greenskins"	15-27	27	;
	range		"greenskins"	5-16	16	;
	range	2	"greenskins"	1-9	9	;
}						
						
party						
{						
	range	2	"greenskins"	5-16	16	;
	range	2	"greenskins"	5-16	16	;
}						
						
party						
{						
	range		"greenskins"	15-27	27	;
	range	2	"greenskins"	5-16	16	;
}						
						
party						
{						
	range		"greenskins_giant"	13-33	33	;
	range	2	"greenskins"	1-9	9	;
}						
						
party						
{						
	range	2	"greenskins"	1-9	9	;
	range		"greenskins"	5-16	16	;
	melee	2	"greenskins"	5-16	16	;
}						
						
party						
{						
	range	2	"greenskins"	1-9	9	;
	range		"greenskins"	5-16	16	;
	melee		"greenskins"	19-30	30	;
}						
						
party						
{						
	range	2	"greenskins"	15-27	27	;
}						
						
party						
{						
	range		"greenskins_giant"	13-40	40	;
}						
						
party						
{						
	range		"greenskins_giant"	15-27	27	;
	melee	2	"greenskins"	1-9	9	;
}						
						
party						
{						
	range	2	"greenskins"	1-9	9	;
	melee		"greenskins_giant"	21-33	33	;
}						
						
party						
{						
	range	2	"greenskins"	5-16	16	;
	melee	2	"greenskins"	5-16	16	;
}						
						
party						
{						
	range	2	"greenskins"	5-16	16	;
	melee		"greenskins"	18-30	30	;
}						
						
party						
{						
	range		"greenskins"	18-30	30	;
	melee	2	"greenskins"	5-16	16	;
}						
						
party						
{						
	range		"greenskins"	5-16	16	;
	melee		"greenskins"	18-30	30	;
	melee	2	"greenskins"	1-9	9	;
}						
						
party						
{						
	range		"greenskins"	5-16	16	;
	melee	2	"greenskins"	5-16	16	;
	melee	2	"greenskins"	1-9	9	;
}						
						
party						
{						
	range	2	"greenskins"	5-16	16	;
	melee		"greenskins"	5-16	16	;
	melee	2	"greenskins"	1-9	9	;
}						

rewards "dungeon_lvl_3";
