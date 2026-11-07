// Dungeon level 3

id	"dragon_altar_tower";

scene	"Resources\\Hexagons\\Models\\Arena\\arena_back_dungeon\\arena_back_dungeon_01_hexmap.scene";

// party
// unit "id" lvlcap;
// or
// class amount "unit_side" level lvlcap;
						
party						
{						
	melee		"legions_giant"	22-40	40	;
}						
						
party						
{						
	melee	2	"legions"	1-9	9	;
	melee	2	"legions_giant"	5-16	16	;
}						
						
party						
{						
	melee	2	"legions_giant"	1-9	9	;
	melee		"legions_giant"	18-30	30	;
}						
						
party						
{						
	melee	2	"legions"	1-9	9	;
	melee		"legions"	5-17	17	;
	caster	2	"legions"	5-16	16	;
}						
						
party						
{						
	melee	2	"legions"	1-9	9	;
	melee		"legions_giant"	18-30	30	;
	caster	2	"legions"	1-9	9	;
}						
						
party						
{						
	melee	2	"legions"	5-17	17	;
	caster	2	"legions"	5-16	16	;
}						
						
party						
{						
	melee		"legions_giant"	18-30	30	;
	caster	2	"legions"	5-16	16	;
}						
						
party						
{						
	melee	2	"legions"	1-9	9	;
	caster		"legions_giant"	16-28	28	;
}						
						
party						
{						
	melee	2	"legions_giant"	1-5	5	;
	caster		"legions_giant"	16-28	28	;
}						
						
party						
{						
	melee	2	"legions"	1-9	9	;
	caster		"legions"	18-30	30	;
	caster	2	"legions"	1-9	9	;
}						
						
party						
{						
	melee		"legions_giant"	18-30	30	;
	caster		"legions"	18-30	30	;
	caster	2	"legions"	1-9	9	;
}						
						
party						
{						
	caster	2	"legions"	5-16	16	;
	caster		"legions"	18-30	30	;
}						
						
party						
{						
	caster	2	"legions"	1-9	9	;
	caster		"legions_giant"	16-28	28	;
}						
						
party						
{						
	caster	2	"legions"	1-9	9	;
	caster	2	"legions"	5-16	16	;
	range		"legions_giant"	5-16	16	;
}						
						
party						
{						
	caster	2	"legions"	18-30	30	;
}						
						
party						
{						
	caster		"legions_giant"	28-40	40	;
}						
						
party						
{						
	caster	2	"legions"	5-16	16	;
	range	2	"legions_giant"	5-16	16	;
}						
						
party						
{						
	caster		"legions_giant"	16-28	28	;
	range	2	"legions_giant"	1-9	9	;
}						
						
party						
{						
	caster	2	"legions"	1-9	9	;
	range		"legions_giant"	5-16	16	;
	range	2	"legions_giant"	1-9	9	;
}						
						
party						
{						
	range		"legions_giant"	13-25	25	;
	range	2	"legions_giant"	5-16	16	;
}						
						
party						
{						
	range	2	"legions_giant"	1-9	9	;
	range		"legions_giant"	5-16	16	;
	melee	2	"legions"	5-17	17	;
}						
						
party						
{						
	range	2	"legions_giant"	1-9	9	;
	range		"legions_giant"	5-16	16	;
	melee		"legions_giant"	18-30	30	;
}						
						
party						
{						
	range	2	"legions_giant"	13-25	25	;
}						
						
party						
{						
	range	2	"legions_giant"	5-16	16	;
	melee		"legions_giant"	18-30	30	;
}						
						
party						
{						
	range	2	"legions_giant"	5-16	16	;
	melee	2	"legions_giant"	5-17	17	;
}						
						
party						
{						
	range		"legions_giant"	5-16	16	;
	melee	2	"legions"	5-17	17	;
	melee	2	"legions_giant"	1-9	9	;
}						
						
party						
{						
	range		"legions_giant"	5-16	16	;
	melee		"legions"	13-25	25	;
	melee	2	"legions_giant"	1-9	9	;
}						
						
party						
{						
	range		"legions_giant"	5-16	16	;
	melee	2	"legions"	5-17	17	;
	melee	2	"legions"	1-9	9	;
}						
						
rewards "dungeon_lvl_3";
